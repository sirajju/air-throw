"""Configuration: this device's identity and the peers assigned to directions.

Stored as JSON at ~/.airthrow/config.json. Stage 1 keeps two directional
slots, "left" and "right", each pointing at one peer (name + ip + port).
"""

from __future__ import annotations

import json
import socket
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import DEFAULT_PORT

CONFIG_DIR = Path.home() / ".airthrow"
CONFIG_PATH = CONFIG_DIR / "config.json"
RECEIVED_DIR = CONFIG_DIR / "Received"

SLOTS = ("left", "right")


@dataclass
class Peer:
    name: str
    ip: str
    port: int = DEFAULT_PORT
    # Position on the "canvas" map (normalised 0..1, centre 0.5,0.5 = you).
    # None until placed in the canvas editor. Used by the canvas gesture.
    x: float | None = None
    y: float | None = None

    def url(self) -> str:
        return f"http://{self.ip}:{self.port}"


@dataclass
class Config:
    # Friendly name other devices see when they receive from us.
    device_name: str = field(default_factory=socket.gethostname)
    # Port our own receiver listens on.
    listen_port: int = DEFAULT_PORT
    # How far the hand must travel (fraction of frame width) to count as a
    # swipe. Lower = easier to trigger. Tunable with `set-sensitivity`.
    swipe_min_distance: float = 0.22
    # Open a received image in the default viewer as soon as it arrives.
    auto_open: bool = True
    # Block re-sending the same file to the same peer within this many seconds.
    resend_block_seconds: float = 10.0
    # Gesture mode: "swipe" (whole-hand flick, default), "palm" (hold open hand
    # to a side), "two" (two-finger swipe), "grab" (fist pick-and-throw), or
    # "canvas" (swipe toward a peer on a map you lay out).
    gesture_mode: str = "swipe"
    # Flash an animated gradient border around the sent window on each throw.
    show_send_animation: bool = True
    # Which border style to use (see send_overlay.STYLES): aurora/rainbow/...
    send_animation_style: str = "aurora"
    # direction slot -> Peer (or missing if unassigned). Used for the fast
    # 2-device path (swipe left/right sends straight away).
    peers: dict[str, Peer] = field(default_factory=dict)
    # Extra named devices beyond left/right. When the total number of devices
    # reaches picker_threshold, a swipe opens an on-screen picker instead of
    # sending immediately.
    devices: list[Peer] = field(default_factory=list)
    # How many total devices before a swipe shows the picker (minimum 2).
    picker_threshold: int = 3

    # ---- persistence -------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            cfg = cls()
            cfg.save()
            return cfg
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        peers = {
            slot: Peer(**data)
            for slot, data in raw.get("peers", {}).items()
            if slot in SLOTS and data
        }
        return cls(
            device_name=raw.get("device_name", socket.gethostname()),
            listen_port=raw.get("listen_port", DEFAULT_PORT),
            swipe_min_distance=float(raw.get("swipe_min_distance", 0.22)),
            auto_open=bool(raw.get("auto_open", True)),
            resend_block_seconds=float(raw.get("resend_block_seconds", 10.0)),
            gesture_mode=raw.get("gesture_mode", "swipe"),
            show_send_animation=bool(raw.get("show_send_animation", True)),
            send_animation_style=raw.get("send_animation_style", "aurora"),
            peers=peers,
            devices=[Peer(**d) for d in raw.get("devices", []) if d],
            picker_threshold=max(2, int(raw.get("picker_threshold", 3))),
        )

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        RECEIVED_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "device_name": self.device_name,
            "listen_port": self.listen_port,
            "swipe_min_distance": self.swipe_min_distance,
            "auto_open": self.auto_open,
            "resend_block_seconds": self.resend_block_seconds,
            "gesture_mode": self.gesture_mode,
            "show_send_animation": self.show_send_animation,
            "send_animation_style": self.send_animation_style,
            "peers": {slot: asdict(peer) for slot, peer in self.peers.items()},
            "devices": [asdict(d) for d in self.devices],
            "picker_threshold": self.picker_threshold,
        }
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ---- mutation ----------------------------------------------------
    def set_peer(self, slot: str, name: str, ip: str, port: int = DEFAULT_PORT) -> None:
        if slot not in SLOTS:
            raise ValueError(f"slot must be one of {SLOTS}, got {slot!r}")
        self.peers[slot] = Peer(name=name, ip=ip, port=port)
        self.save()

    def clear_peer(self, slot: str) -> None:
        self.peers.pop(slot, None)
        self.save()

    def peer_for(self, direction: str) -> Peer | None:
        return self.peers.get(direction)

    # ---- multi-device --------------------------------------------------
    def add_device(self, name: str, ip: str, port: int = DEFAULT_PORT) -> None:
        """Add (or update by name) an extra device for the picker menu."""
        self.devices = [d for d in self.devices if d.name != name]
        self.devices.append(Peer(name=name, ip=ip, port=port))
        self.save()

    def remove_device(self, name: str) -> bool:
        before = len(self.devices)
        self.devices = [d for d in self.devices if d.name != name]
        self.save()
        return len(self.devices) != before

    def all_devices(self) -> list[Peer]:
        """Every target to choose from: left, right, then extras, de-duped by
        address. This is what the on-screen picker lists."""
        ordered: list[Peer] = []
        for slot in SLOTS:
            peer = self.peers.get(slot)
            if peer:
                ordered.append(peer)
        ordered.extend(self.devices)
        seen: set[tuple[str, int]] = set()
        unique: list[Peer] = []
        for p in ordered:
            key = (p.ip, p.port)
            if key not in seen:
                seen.add(key)
                unique.append(p)
        return unique

    def use_picker(self) -> bool:
        """True when there are at least `picker_threshold` devices, so a swipe
        should open the picker instead of sending straight to a left/right slot."""
        return len(self.all_devices()) >= max(2, self.picker_threshold)

    # ---- canvas map ----------------------------------------------------
    def positioned_targets(self) -> list[Peer]:
        """Devices that have been placed on the canvas map (have x,y)."""
        return [p for p in self.all_devices()
                if p.x is not None and p.y is not None]

    def set_position(self, name: str, x: float, y: float) -> bool:
        """Place the peer/device named `name` at (x,y) on the map (0..1)."""
        hit = False
        for peer in list(self.peers.values()) + self.devices:
            if peer.name == name:
                peer.x, peer.y = x, y
                hit = True
        self.save()
        return hit


def local_ip() -> str:
    """Best-effort LAN IP of this machine (the address peers should target)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packets are actually sent; this just picks the outbound interface.
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
