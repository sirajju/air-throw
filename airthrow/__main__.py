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
from .logs import LOG_PATH, setup_logging


def cmd_run(args):
    from .app import run  # imported lazily so config commands don't need cv2
    run(camera_index=args.camera, debug=args.debug)


def cmd_tray(args):
    from .tray import run_tray
    run_tray(camera_index=args.camera)


def cmd_gui(args):
    from .gui import run_gui
    run_gui(camera_index=args.camera)


def cmd_daemon(args):
    from .app import run_daemon
    run_daemon(camera_index=args.camera)


def _ipmsg_client():
    from .ipmsg import IpmsgClient
    cfg = Config.load()
    c = IpmsgClient(user_name=cfg.device_name,
                    on_message=lambda p, t: print(f"\n[msg] {p.label} ({p.ip}): {t}"),
                    on_peer=lambda p: None)
    c.start()
    return c


def cmd_ipmsg_scan(args):
    """Auto-discover IP Messenger peers on the LAN (no manual IPs)."""
    import time
    c = _ipmsg_client()
    if not c.bound:
        c.close()
        sys.exit("Port 2425 is in use by IP Messenger on THIS machine. Close it "
                 "first — discovery needs that port (AirThrow replaces ipmsg here).")
    print(f"Discovering IP Messenger peers for {args.seconds}s "
          f"(everyone running ipmsg answers)...")
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    peers = c.list_peers()
    c.close()
    if not peers:
        print("No peers found. Are others running IP Messenger on this LAN?")
        return
    print(f"\nFound {len(peers)} peer(s):")
    for p in peers:
        print(f"  {p.ip:16} {p.label}")


def cmd_ipmsg_msg(args):
    import time
    c = _ipmsg_client()
    time.sleep(0.4)  # let the socket settle
    c.send_message(args.ip, args.text)
    mode = "" if c.bound else " (send-only; no delivery confirmation)"
    print(f"Message sent to {args.ip}{mode}.")
    time.sleep(0.6)  # allow the ack to arrive
    c.close()


def cmd_ipmsg_canvas(args):
    """Discover ipmsg peers, lay them on the canvas, and let you pick with your
    HAND (reach + hold) — simulation only, nothing is sent."""
    import math
    import time

    from .app import SimPeer, run_canvas_sim
    c = _ipmsg_client()
    if not c.bound:
        c.close()
        sys.exit("Port 2425 is held by IP Messenger here — close it so discovery "
                 "can run, then retry.")
    print(f"Discovering IP Messenger peers for {args.seconds}s...")
    time.sleep(args.seconds)
    peers = c.list_peers()
    c.close()  # free 2425 before the camera starts (camera doesn't need it)

    if peers:
        names = [p.label for p in peers]
    else:
        print("No peers found — showing a sample layout instead.")
        names = ["Reception-PC", "Salah", "Nabeel", "Accounts", "Meeting-Room"]

    # Auto-place around you (centre): even angles, radius staggered so they sit
    # at varied distances instead of a perfect circle.
    n = len(names)
    targets = []
    for i, name in enumerate(names):
        ang = -math.pi / 2 + i * (2 * math.pi / max(1, n))
        radius = 0.30 + 0.06 * (i % 2)
        targets.append(SimPeer(name, round(0.5 + radius * math.cos(ang), 3),
                               round(0.5 + radius * math.sin(ang), 3)))

    print(f"Placed {n} peer(s) on the canvas. Reach your open hand toward one "
          "and HOLD to select — nothing is sent. Press q/Esc to quit.")
    run_canvas_sim(targets, camera_index=args.camera)
    print("Simulation ended. Nothing was sent.")


def cmd_ipmsg_send(args):
    import time
    from pathlib import Path
    path = Path(args.file)
    if not path.exists():
        sys.exit(f"No such file: {path}")
    c = _ipmsg_client()
    if not c.bound:
        c.close()
        sys.exit("Port 2425 is in use by IP Messenger on THIS machine. File "
                 "sending needs that port — close IP Messenger here and retry.")
    time.sleep(0.4)
    c.send_file(args.ip, path, args.text or "")
    print(f"Offered {path.name} to {args.ip}. Keeping the transfer server up...")
    print("Leave this running until the peer has accepted the file. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    c.close()


def cmd_service(args):
    from . import autostart
    if args.action == "install":
        where = autostart.service_install()
        print(f"Service installed and started -> {where}")
        print("It will start automatically on login. Check: airthrow service status")
    elif args.action == "uninstall":
        ok = autostart.service_uninstall()
        print("Service removed." if ok else "Service was not installed.")
    else:  # status
        print(f"Service: {autostart.service_status()}")


def cmd_set_animation(args):
    cfg = Config.load()
    cfg.show_send_animation = args.state == "on"
    cfg.save()
    print(f"Send animation (gradient border): "
          f"{'on' if cfg.show_send_animation else 'off'}")


def cmd_set_animation_style(args):
    from .send_overlay import STYLE_NAMES
    cfg = Config.load()
    cfg.send_animation_style = args.style
    cfg.save()
    print(f"Send animation style set to '{args.style}'. "
          f"Preview any with:  airthrow preview")
    print(f"Available styles: {', '.join(STYLE_NAMES)}")


def cmd_preview(args):
    """Simulate the send border with NO camera/peer/image — just watch it."""
    import time

    from . import send_overlay
    from .foreground_image import foreground_rect

    rect = None
    if args.window:
        rect = foreground_rect()  # frame whatever window is focused now

    if args.style:
        styles = [args.style]
    else:
        styles = list(send_overlay.STYLE_NAMES)  # cycle through all of them

    print("Previewing send border (no image is sent). Watch your screen...")
    for _ in range(max(1, args.loops)):
        for name in styles:
            print(f"  style: {name}")
            send_overlay.flash(rect, duration=args.duration, style=name)
            time.sleep(args.duration + 0.35)

    # Also demo the flying object (sender throw + receiver arrival).
    print("  flying object: throw right, throw left, arrive from left, arrive from right")
    send_overlay.fly_out(None, "right"); time.sleep(1.0)
    send_overlay.fly_out(None, "left"); time.sleep(1.0)
    send_overlay.fly_in(None, "left"); time.sleep(1.4)
    send_overlay.fly_in(None, "right"); time.sleep(1.4)
    print("Preview done. Pick one with:  airthrow set-animation-style <name>")


def cmd_set_gesture(args):
    cfg = Config.load()
    cfg.gesture_mode = args.mode
    cfg.save()
    msg = {
        "palm": "Gesture: palm-reach (hold your OPEN hand to the left/right side "
                "~0.5s — most reliable, no fast motion needed).",
        "two": "Gesture: two-finger swipe (hold index+middle up, flick left/right).",
        "grab": "Gesture: grab-and-throw (make a fist to pick up, move, open hand to send).",
        "swipe": "Gesture: swipe (flick your whole hand left/right).",
        "canvas": "Gesture: canvas map (reach your open hand toward a device on "
                  "the map you laid out, then hold to send). Set positions in the GUI.",
    }
    print(msg[args.mode])


def cmd_autostart(args):
    from . import autostart
    if args.action == "on":
        where = autostart.enable()
        print(f"Autostart enabled -> {where}")
    elif args.action == "off":
        removed = autostart.disable()
        print("Autostart disabled." if removed else "Autostart was not set.")
    else:  # status
        print(f"Autostart: {'on' if autostart.status() else 'off'}")


def cmd_camtest(args):
    from .app import probe_cameras
    probe_cameras()


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
    from . import autostart
    print(f"  gesture     : {cfg.gesture_mode} (swipe = flick; palm = hold open "
          f"hand to a side; two = two-finger swipe; grab = fist; canvas = map)")
    print(f"  autostart   : {'on' if autostart.status() else 'off'}")
    print(f"  sensitivity : {cfg.swipe_min_distance:.2f} (swipe travel needed; lower = easier)")
    print(f"  auto-open   : {'on' if cfg.auto_open else 'off'} (open received images automatically)")
    print(f"  resend-block: {cfg.resend_block_seconds:.0f}s (ignore same file->same peer repeats; 0 = off)")
    print(f"  log file    : {LOG_PATH}")


def cmd_logs(args):
    if not LOG_PATH.exists():
        print(f"No log yet at {LOG_PATH} (run AirThrow first).")
        return
    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"# {LOG_PATH}  (last {min(args.lines, len(lines))} of {len(lines)} lines)")
    for line in lines[-args.lines:]:
        print(line)


def cmd_set_name(args):
    cfg = Config.load()
    cfg.device_name = args.name
    cfg.save()
    print(f"Device name set to {args.name!r}")


def cmd_set_sensitivity(args):
    if not 0.02 <= args.distance <= 0.9:
        sys.exit("distance should be between 0.02 (ultra easy) and 0.9 (very hard)")
    cfg = Config.load()
    cfg.swipe_min_distance = args.distance
    cfg.save()
    print(f"Swipe sensitivity set to {args.distance:.2f} "
          f"(hand must travel {args.distance*100:.0f}% of frame width).")


def cmd_set_resend_block(args):
    if not 0 <= args.seconds <= 600:
        sys.exit("seconds should be between 0 (off) and 600")
    cfg = Config.load()
    cfg.resend_block_seconds = args.seconds
    cfg.save()
    if args.seconds == 0:
        print("Redundant-send guard disabled (every swipe sends).")
    else:
        print(f"Redundant-send window set to {args.seconds:.0f}s "
              f"(same file -> same peer is skipped within this time).")


def cmd_set_auto_open(args):
    cfg = Config.load()
    cfg.auto_open = args.state == "on"
    cfg.save()
    print(f"Auto-open received images: {'on' if cfg.auto_open else 'off'}")


def cmd_devices(args):
    cfg = Config.load()
    print("Extra devices (beyond left/right):")
    if cfg.devices:
        for d in cfg.devices:
            print(f"  - {d.name} @ {d.ip}:{d.port}")
    else:
        print("  (none)")
    all_d = cfg.all_devices()
    print(f"\nAll targets ({len(all_d)}):", ", ".join(d.name for d in all_d) or "(none)")
    print(f"Picker threshold: {cfg.picker_threshold} devices "
          f"(change with set-picker-threshold).")
    if cfg.use_picker():
        print(f"{len(all_d)} devices: a swipe opens the picker — swipe left/right "
              "to move the highlight, hold to send.")
    else:
        print("Below threshold: swipe left/right sends straight to that slot.")


def cmd_set_picker_threshold(args):
    if not 2 <= args.count <= 50:
        sys.exit("threshold should be between 2 and 50")
    cfg = Config.load()
    cfg.picker_threshold = args.count
    cfg.save()
    print(f"Picker shows when there are {args.count}+ devices "
          f"(currently {len(cfg.all_devices())}; picker "
          f"{'ON' if cfg.use_picker() else 'off'}).")


def cmd_add_device(args):
    cfg = Config.load()
    cfg.add_device(args.name, args.ip, args.port)
    print(f"Added device: {args.name} @ {args.ip}:{args.port}")
    print(f"Now {len(cfg.all_devices())} targets; "
          f"picker {'ON' if cfg.use_picker() else 'off (need 3+)'}.")


def cmd_remove_device(args):
    cfg = Config.load()
    ok = cfg.remove_device(args.name)
    print(f"Removed {args.name!r}" if ok else f"No device named {args.name!r}")


def cmd_preview_select(args):
    """Simulate the device picker (no send) so you can see it remotely."""
    import time

    from . import send_overlay
    cfg = Config.load()
    names = [d.name for d in cfg.all_devices()]
    if len(names) < 2:
        names = ["Living-Room-PC", "Studio-PC", "Salah-Laptop", "Bedroom-PC"]
        print("(fewer than 2 real devices; showing sample names)")
    send_overlay.selector_show(names, title="Throw to…",
                               style=cfg.send_animation_style)
    print("Picker shown. Cycling highlight; watch your screen...")
    for i in range(len(names) * 2):
        send_overlay.selector_set(i % len(names))
        time.sleep(0.7)
    time.sleep(0.4)
    send_overlay.selector_hide()
    print("Picker closed.")


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
    r.add_argument("--debug", action="store_true",
                   help="verbose logs + on-screen diagnostic overlay")
    r.set_defaults(func=cmd_run)

    tr = sub.add_parser("tray", help="run in the background with a tray icon")
    tr.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    tr.set_defaults(func=cmd_tray)

    g = sub.add_parser("gui", help="open the desktop control-panel window")
    g.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    g.set_defaults(func=cmd_gui)

    dm = sub.add_parser("daemon",
                        help="run headless in the background (no window/tray)")
    dm.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    dm.set_defaults(func=cmd_daemon)

    sv = sub.add_parser("service",
                        help="install/remove the background service (auto-start on login)")
    sv.add_argument("action", choices=("install", "uninstall", "status"))
    sv.set_defaults(func=cmd_service)

    # IP Messenger interop (auto-discovery + messages + files).
    isc = sub.add_parser("ipmsg-scan",
                         help="auto-discover IP Messenger peers on the LAN")
    isc.add_argument("--seconds", type=float, default=3.0, help="listen time")
    isc.set_defaults(func=cmd_ipmsg_scan)

    imsg = sub.add_parser("ipmsg-msg", help="send a text message to an ipmsg peer")
    imsg.add_argument("ip")
    imsg.add_argument("text")
    imsg.set_defaults(func=cmd_ipmsg_msg)

    ican = sub.add_parser("ipmsg-canvas",
                          help="discover ipmsg peers; pick one with your hand on the canvas (simulation, no send)")
    ican.add_argument("--seconds", type=float, default=4.0, help="discovery time")
    ican.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    ican.set_defaults(func=cmd_ipmsg_canvas)

    isend = sub.add_parser("ipmsg-send", help="send any file to an ipmsg peer")
    isend.add_argument("ip")
    isend.add_argument("file")
    isend.add_argument("text", nargs="?", default="", help="optional message")
    isend.set_defaults(func=cmd_ipmsg_send)

    sg = sub.add_parser("set-gesture", help="choose gesture: two, grab or swipe")
    sg.add_argument("mode", choices=("swipe", "palm", "two", "grab", "canvas"))
    sg.set_defaults(func=cmd_set_gesture)

    au = sub.add_parser("autostart", help="start on login: on/off/status")
    au.add_argument("action", choices=("on", "off", "status"))
    au.set_defaults(func=cmd_autostart)

    sub.add_parser("info", help="show this device's name/IP/port").set_defaults(func=cmd_info)

    sub.add_parser(
        "camtest", help="probe camera indices and report which deliver frames"
    ).set_defaults(func=cmd_camtest)

    sub.add_parser(
        "selftest", help="load the hand model & process a frame (no camera)"
    ).set_defaults(func=cmd_selftest)

    lg = sub.add_parser("logs", help="show the tail of the log file")
    lg.add_argument("-n", "--lines", type=int, default=40, help="lines to show")
    lg.set_defaults(func=cmd_logs)

    sn = sub.add_parser("set-name", help="set friendly device name")
    sn.add_argument("name")
    sn.set_defaults(func=cmd_set_name)

    ss = sub.add_parser("set-sensitivity",
                        help="swipe travel needed (0.05 easy .. 0.9 hard)")
    ss.add_argument("distance", type=float)
    ss.set_defaults(func=cmd_set_sensitivity)

    ao = sub.add_parser("set-auto-open",
                        help="open received images automatically (on/off)")
    ao.add_argument("state", choices=("on", "off"))
    ao.set_defaults(func=cmd_set_auto_open)

    an = sub.add_parser("set-animation",
                        help="gradient send border on each throw (on/off)")
    an.add_argument("state", choices=("on", "off"))
    an.set_defaults(func=cmd_set_animation)

    from .send_overlay import STYLE_NAMES
    ast = sub.add_parser("set-animation-style",
                         help=f"border style: {', '.join(STYLE_NAMES)}")
    ast.add_argument("style", choices=STYLE_NAMES)
    ast.set_defaults(func=cmd_set_animation_style)

    pv = sub.add_parser("preview",
                        help="simulate the send border (no image sent) to see the styles")
    pv.add_argument("style", nargs="?", choices=STYLE_NAMES, default=None,
                    help="one style to preview; omit to cycle through all")
    pv.add_argument("--loops", type=int, default=1, help="times to repeat")
    pv.add_argument("--duration", type=float, default=1.6, help="seconds per flash")
    pv.add_argument("--window", action="store_true",
                    help="frame the focused window instead of the whole screen")
    pv.set_defaults(func=cmd_preview)

    rb = sub.add_parser("set-resend-block",
                        help="seconds to ignore same file->same peer repeats (0=off)")
    rb.add_argument("seconds", type=float)
    rb.set_defaults(func=cmd_set_resend_block)

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

    sub.add_parser("devices",
                   help="list all targets; 3+ enables the on-screen picker"
                   ).set_defaults(func=cmd_devices)

    ad = sub.add_parser("add-device",
                        help="add an extra named device (beyond left/right)")
    ad.add_argument("name")
    ad.add_argument("ip")
    ad.add_argument("port", nargs="?", type=int, default=DEFAULT_PORT)
    ad.set_defaults(func=cmd_add_device)

    rd = sub.add_parser("remove-device", help="remove an extra device by name")
    rd.add_argument("name")
    rd.set_defaults(func=cmd_remove_device)

    pt = sub.add_parser("set-picker-threshold",
                        help="how many devices before a swipe shows the picker (>=2)")
    pt.add_argument("count", type=int)
    pt.set_defaults(func=cmd_set_picker_threshold)

    sub.add_parser("preview-select",
                   help="simulate the device picker (no send) to see it"
                   ).set_defaults(func=cmd_preview_select)

    pg = sub.add_parser("ping", help="check a slot peer is reachable")
    pg.add_argument("slot", choices=SLOTS)
    pg.set_defaults(func=cmd_ping)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    setup_logging(debug=getattr(args, "debug", False))
    args.func(args)


if __name__ == "__main__":
    main()
