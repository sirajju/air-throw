"""Command-line entrypoint: `python -m airthrow <command>`.

Commands:
  run                         Start camera + gesture detection + receiver.
  info                        Show this device's name, IP and port.
  set-name <name>             Set the friendly device name peers see.
  peers                       List the left/right slot assignments.
  set-peer <slot> <name> <ip> [port]   Assign a peer to 'left' or 'right'.
  clear-peer <slot>           Unassign a slot.
  ping <slot>                 Check whether a slot's peer is reachable.
"""

from __future__ import annotations

import argparse
import sys

from . import DEFAULT_PORT, __version__
from .config import SLOTS, Config, local_ip


def cmd_run(args):
    from .app import run  # imported lazily so config commands don't need cv2
    run(camera_index=args.camera)


def cmd_selftest(args):
    """Load the hand model and process one blank frame (no camera needed)."""
    import numpy as np

    from .gestures import SwipeDetector
    det = SwipeDetector(on_swipe=lambda d: None)
    frame, direction = det.process(np.zeros((480, 640, 3), dtype=np.uint8))
    det.close()
    print(f"OK: hand model loaded and processed a frame (direction={direction}).")


def cmd_info(args):
    cfg = Config.load()
    print(f"AirThrow {__version__}")
    print(f"  device name : {cfg.device_name}")
    print(f"  LAN address : {local_ip()}:{cfg.listen_port}")
    print(f"  peers to add on OTHER machines: --ip {local_ip()} --port {cfg.listen_port}")


def cmd_set_name(args):
    cfg = Config.load()
    cfg.device_name = args.name
    cfg.save()
    print(f"Device name set to {args.name!r}")


def cmd_peers(args):
    cfg = Config.load()
    for slot in SLOTS:
        peer = cfg.peers.get(slot)
        if peer:
            print(f"  {slot:5} -> {peer.name} @ {peer.ip}:{peer.port}")
        else:
            print(f"  {slot:5} -> (unassigned)")


def cmd_set_peer(args):
    if args.slot not in SLOTS:
        sys.exit(f"slot must be one of {SLOTS}")
    cfg = Config.load()
    cfg.set_peer(args.slot, args.name, args.ip, args.port)
    print(f"{args.slot} -> {args.name} @ {args.ip}:{args.port}")


def cmd_clear_peer(args):
    cfg = Config.load()
    cfg.clear_peer(args.slot)
    print(f"Cleared {args.slot}")


def cmd_ping(args):
    from .transfer import check_peer
    cfg = Config.load()
    peer = cfg.peers.get(args.slot)
    if not peer:
        sys.exit(f"No peer assigned to {args.slot}")
    ok = check_peer(peer.url())
    print(f"{peer.name} @ {peer.url()} : {'reachable' if ok else 'NOT reachable'}")
    sys.exit(0 if ok else 1)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="airthrow", description="Throw images across the air.")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="start camera + gestures + receiver")
    r.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    r.set_defaults(func=cmd_run)

    sub.add_parser("info", help="show this device's name/IP/port").set_defaults(func=cmd_info)

    sub.add_parser(
        "selftest", help="load the hand model & process a frame (no camera)"
    ).set_defaults(func=cmd_selftest)

    sn = sub.add_parser("set-name", help="set friendly device name")
    sn.add_argument("name")
    sn.set_defaults(func=cmd_set_name)

    sub.add_parser("peers", help="list slot assignments").set_defaults(func=cmd_peers)

    sp = sub.add_parser("set-peer", help="assign a peer to a direction slot")
    sp.add_argument("slot", choices=SLOTS)
    sp.add_argument("name")
    sp.add_argument("ip")
    sp.add_argument("port", nargs="?", type=int, default=DEFAULT_PORT)
    sp.set_defaults(func=cmd_set_peer)

    cp = sub.add_parser("clear-peer", help="unassign a slot")
    cp.add_argument("slot", choices=SLOTS)
    cp.set_defaults(func=cmd_clear_peer)

    pg = sub.add_parser("ping", help="check a slot peer is reachable")
    pg.add_argument("slot", choices=SLOTS)
    pg.set_defaults(func=cmd_ping)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
