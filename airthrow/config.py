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
    # direction slot -> Peer (or missing if unassigned)
    peers: dict[str, Peer] = field(default_factory=dict)

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
            peers=peers,
        )

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        RECEIVED_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "device_name": self.device_name,
            "listen_port": self.listen_port,
            "swipe_min_distance": self.swipe_min_distance,
            "auto_open": self.auto_open,
            "peers": {slot: asdict(peer) for slot, peer in self.peers.items()},
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
