"""IP Messenger (ipmsg) interop — auto-discovery, messages, and file transfer.

IP Messenger is a long-standing LAN tool (UDP+TCP port 2425) that most offices
already run, so speaking its protocol lets AirThrow **auto-discover every peer**
(no typing IPs) and **send any file type or a text message** straight to the
clients colleagues already have open.

Protocol (ipmsg.org): a UDP packet is a colon-delimited string
``ver:packetNo:user:host:command:payload`` where the low byte of ``command`` is
the command and the high bits are option flags. Presence is broadcast with
BR_ENTRY / answered with ANSENTRY / withdrawn with BR_EXIT. A file is announced
in a SENDMSG with the FILEATTACHOPT flag (payload = ``text\\0 fileID:name:size:
mtime:attr:\\a``); the receiver then opens TCP 2425 and sends GETFILEDATA
(``packetID:fileID:offset`` in hex) to pull the bytes, which we stream back.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .logs import get_logger

log = get_logger()

IPMSG_PORT = 2425
PROTO_VER = "1"

# Commands (low byte of the command word).
IPMSG_NOOPERATION = 0x00000000
IPMSG_BR_ENTRY = 0x00000001
IPMSG_BR_EXIT = 0x00000002
IPMSG_ANSENTRY = 0x00000003
IPMSG_SENDMSG = 0x00000020
IPMSG_RECVMSG = 0x00000021
IPMSG_GETINFO = 0x00000040
IPMSG_SENDINFO = 0x00000041
IPMSG_GETFILEDATA = 0x00000060
IPMSG_RELEASEFILES = 0x00000061

# Option flags (OR'd into the high bits of the command word).
IPMSG_SENDCHECKOPT = 0x00000100
IPMSG_FILEATTACHOPT = 0x00200000
IPMSG_ENCRYPTOPT = 0x00400000
IPMSG_UTF8OPT = 0x00800000

# File-type attribute.
IPMSG_FILE_REGULAR = 0x00000001


@dataclass
class IpmsgPeer:
    ip: str
    user: str = ""      # login user from the packet header
    host: str = ""      # hostname from the packet header
    nick: str = ""      # display nickname (from the entry payload)
    group: str = ""     # group name (from the entry payload)
    last_seen: float = field(default_factory=time.time)

    @property
    def label(self) -> str:
        """How IP Messenger lists a user: nickname, with group in brackets."""
        name = self.nick or self.user or self.host or self.ip
        return f"{name} ({self.group})" if self.group else name


def _utf8(s: str) -> bytes:
    return s.encode("utf-8", "replace")


def broadcast_addresses() -> list[str]:
    """Directed broadcast address for every real IPv4 interface.

    A plain 255.255.255.255 only leaves one interface, so on a machine with a
    VirtualBox/VMware/WSL adapter the discovery packet can miss the real LAN.
    We compute a.b.c.255 (etc.) per interface so every subnet is reached."""
    out: list[str] = []
    try:
        import psutil
        for snics in psutil.net_if_addrs().values():
            for s in snics:
                if s.family != socket.AF_INET or not s.address or not s.netmask:
                    continue
                if s.address.startswith(("127.", "169.254.")):
                    continue
                try:
                    net = ipaddress.IPv4Network(f"{s.address}/{s.netmask}",
                                                strict=False)
                    out.append(str(net.broadcast_address))
                except ValueError:
                    continue
    except Exception:
        pass
    if "255.255.255.255" not in out:
        out.append("255.255.255.255")
    return out


def _own_ipv4() -> set[str]:
    """This machine's own IPv4 addresses (to skip self in discovery)."""
    ips = {"127.0.0.1"}
    try:
        import psutil
        for snics in psutil.net_if_addrs().values():
            for s in snics:
                if s.family == socket.AF_INET and s.address:
                    ips.add(s.address)
    except Exception:
        pass
    return ips


class IpmsgClient:
    """A minimal IP Messenger node: discovery + send message + send file.

    on_message(peer, text)        — a text message arrived
    on_peer(peer)                 — a peer appeared/updated (discovery)
    """

    def __init__(self, user_name: str | None = None,
                 on_message: Callable[[IpmsgPeer, str], None] | None = None,
                 on_peer: Callable[[IpmsgPeer], None] | None = None,
                 port: int = IPMSG_PORT):
        self.user = user_name or os.environ.get("USERNAME") or "airthrow"
        self.host = socket.gethostname()
        self.port = port
        self.on_message = on_message or (lambda p, t: None)
        self.on_peer = on_peer or (lambda p: None)

        self.peers: dict[str, IpmsgPeer] = {}
        self._own_ips = _own_ipv4()
        self._pkt = int(time.time())
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._udp: socket.socket | None = None
        self._tcp: socket.socket | None = None
        # (packet_no, file_id) -> Path, for serving GETFILEDATA pulls.
        self._offered: dict[tuple[int, int], Path] = {}
        self._threads: list[threading.Thread] = []

    # ---- packet helpers ---------------------------------------------
    def _next_pkt(self) -> int:
        with self._lock:
            self._pkt += 1
            return self._pkt

    def _packet(self, command: int, payload: bytes = b"", pkt_no: int | None = None):
        no = pkt_no if pkt_no is not None else self._next_pkt()
        head = f"{PROTO_VER}:{no}:{self.user}:{self.host}:{command}:"
        return _utf8(head) + payload, no

    # ---- lifecycle ---------------------------------------------------
    def start(self) -> None:
        self.bound = False  # True when we own port 2425 (full mode)
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            self._udp.bind(("0.0.0.0", self.port))
            self.bound = True
        except OSError as exc:
            # Usually the native IP Messenger holds 2425 on this host. We can
            # still SEND messages (from an ephemeral port), but can't receive
            # (discovery) or serve files until that port is free.
            log.warning("ipmsg: UDP %d busy — send-only mode (close IP Messenger "
                        "on this machine for discovery/files): %s", self.port, exc)

        if self.bound:
            self._tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self._tcp.bind(("0.0.0.0", self.port))
                self._tcp.listen(8)
            except OSError as exc:
                log.warning("ipmsg TCP %d unavailable (file pulls disabled): %s",
                            self.port, exc)
                self._tcp = None
            self._spawn(self._udp_loop, "ipmsg-udp")
            if self._tcp:
                self._spawn(self._tcp_loop, "ipmsg-tcp")
            self.announce_entry()
        log.info("ipmsg started as '%s' (%s mode)", self.user,
                 "full" if self.bound else "send-only")

    def _spawn(self, target, name):
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def close(self) -> None:
        self._stop.set()
        try:
            self.broadcast(IPMSG_BR_EXIT)
        except Exception:
            pass
        for s in (self._udp, self._tcp):
            try:
                if s:
                    s.close()
            except Exception:
                pass

    # ---- discovery ---------------------------------------------------
    def announce_entry(self) -> None:
        """Broadcast our presence (BR_ENTRY) so peers register us and reply."""
        self.broadcast(IPMSG_BR_ENTRY | IPMSG_UTF8OPT, _utf8(self.user))

    def broadcast(self, command: int, payload: bytes = b"") -> None:
        data, _ = self._packet(command, payload)
        sent = False
        for addr in broadcast_addresses():  # one per interface subnet
            try:
                self._udp.sendto(data, (addr, self.port))
                sent = True
            except OSError:
                continue
        if not sent:
            try:
                self._udp.sendto(data, ("255.255.255.255", self.port))
            except OSError:
                pass

    def _send(self, ip: str, command: int, payload: bytes = b"",
              pkt_no: int | None = None) -> int:
        data, no = self._packet(command, payload, pkt_no)
        self._udp.sendto(data, (ip, self.port))
        return no

    # ---- sending ----------------------------------------------------
    def send_message(self, ip: str, text: str, want_check: bool = True) -> None:
        cmd = IPMSG_SENDMSG | IPMSG_UTF8OPT
        if want_check:
            cmd |= IPMSG_SENDCHECKOPT
        self._send(ip, cmd, _utf8(text))

    def send_file(self, ip: str, path: Path, text: str = "") -> None:
        """Offer `path` to `ip` and serve it over TCP on GETFILEDATA."""
        path = Path(path)
        size = path.stat().st_size
        mtime = int(path.stat().st_mtime)
        file_id = 0
        # fileID:filename:size(hex):mtime(hex):attr(hex):\a
        attach = (f"{file_id}:{path.name}:{size:x}:{mtime:x}:"
                  f"{IPMSG_FILE_REGULAR:x}:\x07")
        payload = _utf8(text) + b"\x00" + _utf8(attach)
        cmd = IPMSG_SENDMSG | IPMSG_UTF8OPT | IPMSG_FILEATTACHOPT
        no = self._send(ip, cmd, payload)
        with self._lock:
            self._offered[(no, file_id)] = path
        log.info("ipmsg offered %s (%d bytes) to %s", path.name, size, ip)

    # ---- receiving --------------------------------------------------
    def _udp_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self._udp.recvfrom(65536)
            except OSError:
                break
            try:
                self._handle(data, addr[0])
            except Exception as exc:
                log.debug("ipmsg parse error from %s: %s", addr[0], exc)

    def _handle(self, data: bytes, ip: str) -> None:
        # ver:pktno:user:host:command:payload  (payload may contain colons / NUL)
        parts = data.split(b":", 5)
        if len(parts) < 6:
            return
        _, pktno, user, host, command, payload = parts
        try:
            cmd_word = int(command)
        except ValueError:
            return
        base = cmd_word & 0xFF
        text = payload.decode("utf-8", "replace")
        if ip in self._own_ips:
            return  # ignore our own broadcast echoed back
        peer = self._touch_peer(ip, user.decode("utf-8", "replace"),
                                host.decode("utf-8", "replace"))

        if base in (IPMSG_BR_ENTRY, IPMSG_ANSENTRY):
            # Entry/answer payload = "nickname\0group" — the display name and
            # group IP Messenger shows in its user list.
            fields = text.split("\x00")
            nick = fields[0].strip() if fields and fields[0].strip() else ""
            group = fields[1].strip() if len(fields) > 1 else ""
            with self._lock:
                if nick:
                    peer.nick = nick
                if group:
                    peer.group = group
            if base == IPMSG_BR_ENTRY:
                # Answer so they learn about us.
                try:
                    self._send(ip, IPMSG_ANSENTRY | IPMSG_UTF8OPT, _utf8(self.user))
                except Exception:
                    pass
        elif base == IPMSG_BR_EXIT:
            with self._lock:
                self.peers.pop(ip, None)
        elif base == IPMSG_SENDMSG:
            msg = text.split("\x00", 1)[0]  # strip any file-attach section
            if cmd_word & IPMSG_SENDCHECKOPT:
                try:
                    self._send(ip, IPMSG_RECVMSG, pktno)  # ack
                except Exception:
                    pass
            if msg:
                self.on_message(peer, msg)

    def _touch_peer(self, ip, user, host) -> IpmsgPeer:
        with self._lock:
            peer = self.peers.get(ip)
            if peer:
                peer.user, peer.host, peer.last_seen = user, host, time.time()
            else:
                peer = IpmsgPeer(ip=ip, user=user, host=host)
                self.peers[ip] = peer
        self.on_peer(peer)
        return peer

    # ---- TCP file server --------------------------------------------
    def _tcp_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._tcp.accept()
            except OSError:
                break
            threading.Thread(target=self._serve_file, args=(conn,),
                             daemon=True).start()

    def _serve_file(self, conn: socket.socket) -> None:
        try:
            req = conn.recv(1024).decode("utf-8", "replace").strip()
            # GETFILEDATA request body: packetID:fileID:offset  (all hex)
            fields = req.split(":")
            if len(fields) < 2:
                return
            pkt_no = int(fields[0], 16)
            file_id = int(fields[1], 16)
            offset = int(fields[2], 16) if len(fields) > 2 else 0
            with self._lock:
                path = self._offered.get((pkt_no, file_id))
            if not path or not path.exists():
                return
            peer_ip = conn.getpeername()[0] if conn else "?"
            log.info("ipmsg: %s is downloading %s (%.1f MB)...",
                     peer_ip, path.name, path.stat().st_size / 1e6)
            with path.open("rb") as fh:
                fh.seek(offset)
                while not self._stop.is_set():
                    chunk = fh.read(64 * 1024)
                    if not chunk:
                        break
                    conn.sendall(chunk)
            log.info("ipmsg served %s", path.name)
        except Exception as exc:
            log.debug("ipmsg file serve error: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ---- convenience ------------------------------------------------
    def list_peers(self) -> list[IpmsgPeer]:
        with self._lock:
            return sorted(self.peers.values(), key=lambda p: p.label.lower())


def pull_file(ip: str, pkt_no: int, file_id: int, dest: Path,
              port: int = IPMSG_PORT, timeout: float = 30.0) -> int:
    """Download an offered file from an ipmsg sender over TCP (GETFILEDATA)."""
    req = f"{pkt_no:x}:{file_id:x}:0"
    total = 0
    with socket.create_connection((ip, port), timeout=timeout) as s:
        s.sendall(_utf8(req))
        with Path(dest).open("wb") as fh:
            while True:
                chunk = s.recv(64 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                total += len(chunk)
    return total
