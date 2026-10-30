"""Orchestrator: wire the camera + gestures + foreground image + transfer.

The receiver runs in a background thread so the machine can also *receive*.
Webcam frames feed the active gesture detector (grab-and-throw or swipe); a
recognised gesture sends the remembered foreground image to the peer assigned
to that direction, on a worker thread so the camera never stalls.

The camera loop is headless-capable: CLI `run` shows the preview window and
quits on q/ESC, while the system tray drives the same loop with the preview
toggled on demand and stopping via a shared Event.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2

from . import send_overlay, transfer
from .config import Config, local_ip
from .foreground_image import current_image, foreground_rect, foreground_window
from .gestures import (BaseHandDetector, CanvasDetector, GrabDetector,
                       PalmReachDetector, SwipeDetector, TwoFingerDetector)
from .logs import LOG_PATH, get_logger
from .payload import resolve_payload

log = get_logger()

WINDOW_NAME = "AirThrow"
POLL_EVERY = 8  # frames between foreground-image checks (~3-4x/sec)
PICKER_CONFIRM_DELAY = 1.8  # seconds of no swipe before the picker auto-sends

# Canvas (map) gesture tuning.
CANVAS_AIM_MIN_MAG = 0.12     # how far the hand must be off-centre to aim
CANVAS_MAX_ANGLE_DEG = 55     # hand must point within this of a device
CANVAS_DWELL = 1.3            # hold the aim this long to send (deliberate hold)
CANVAS_GRACE_FRAMES = 6       # tolerate this many hand-less frames before hiding
CANVAS_COOLDOWN = 2.0         # min gap between canvas sends


def _angle_diff(a: float, b: float) -> float:
    """Smallest signed difference between two angles (radians), in [-pi, pi]."""
    d = (a - b + math.pi) % (2 * math.pi) - math.pi
    return d


def open_camera(index: int):
    """Open a camera and confirm it actually delivers frames.

    On Windows the default MSMF backend opens some webcams but returns no
    frames; DirectShow (DSHOW) usually works. We try backends in order and
    only accept one whose first read() succeeds.

    Returns (cap, backend_name) or (None, reason).
    """
    if sys.platform.startswith("win"):
        backends = [("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF),
                    ("ANY", cv2.CAP_ANY)]
    else:
        backends = [("V4L2", cv2.CAP_V4L2), ("ANY", cv2.CAP_ANY)]

    opened_but_no_frame = False
    for name, backend in backends:
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                return cap, name
            opened_but_no_frame = True
            log.warning("camera %d opened via %s but gave no frame; next backend",
                        index, name)
        cap.release()

    if opened_but_no_frame:
        return None, "opened but delivered no frames (try another --camera index)"
    return None, "could not open (no camera at this index?)"


class AirThrow:
    def __init__(self, config: Config, camera_index: int = 0, debug: bool = False):
        self.config = config
        self.camera_index = camera_index
        self.debug = debug
        self.last_image: Path | None = None
        # On-screen rect (x, y, w, h) of the window the image was seen in, for
        # the send animation; tracked alongside last_image.
        self.last_image_rect: tuple[int, int, int, int] | None = None
        self._held_image: Path | None = None  # locked at grab-time (grab mode)
        self._held_rect: tuple[int, int, int, int] | None = None

        # Device picker (shown when there are 3+ devices): a swipe opens it,
        # further swipes move the highlight, and after a short dwell with no new
        # swipe it sends to the highlighted device. The dwell timer resets on
        # every swipe, which debounces a flickery gesture so the box is stable.
        self._picker_lock = threading.Lock()
        self._picker_active = False
        self._picker_devices: list = []
        self._picker_index = 0
        self._picker_image: Path | None = None
        self._picker_rect: tuple[int, int, int, int] | None = None
        self._picker_timer: threading.Timer | None = None

        # Canvas (map) gesture state.
        self._canvas_shown = False
        self._canvas_targets: list = []
        self._canvas_aim = -1
        self._canvas_since = 0.0
        self._canvas_last_fire = 0.0
        self._canvas_miss = 0
        # Optional overrides (used by the hand-controlled simulation — leaving
        # them None keeps the normal send behaviour untouched).
        self._gesture_override: str | None = None
        self._canvas_targets_override: list | None = None
        self._canvas_dry_run = False
        self._canvas_send_fn = None  # called (target, image) for real sends
        self.status = "Ready"
        self.status_color = (255, 255, 255)
        self.status_until = 0.0
        self.detector: BaseHandDetector | None = None
        self._fps = 0.0
        self._last_frame_t = 0.0
        # (peer_url, file_path, mtime) -> timestamp of last send, for dedupe.
        self._recent_sends: dict[tuple[str, str, float], float] = {}

        # Control for the camera loop (set by run()/start_background()).
        self._stop = threading.Event()
        self._preview_fn = lambda: True      # whether to show the preview window
        self._quit_on_key = True             # q/ESC quits (CLI) vs hides (tray)
        self._on_preview_key = lambda: None  # called when q pressed in tray mode
        # When set, annotated frames go here (GUI in-window preview) instead of
        # an OpenCV window. Also always exposes the latest frame for feedback.
        self._frame_sink = None
        self.camera_ok = False               # True once frames are flowing
        self.receive_only = False            # True when no usable camera

    # ---- status helpers ---------------------------------------------
    def set_status(self, text: str, color=(255, 255, 255), seconds: float = 4.0):
        self.status = text
        self.status_color = color
        self.status_until = time.time() + seconds

    # ---- events ------------------------------------------------------
    def on_received(self, sender: str, filename: str, path: Path,
                    direction: str = ""):
        self.set_status(f"Received {filename} from {sender}", (0, 220, 0), 6)
        if self.config.show_send_animation:
            try:
                # The sender threw toward `direction`; it flies across the air
                # and enters the receiver from the opposite edge.
                from_dir = {"right": "left", "left": "right"}.get(direction, "right")
                send_overlay.fly_in(path, from_dir)
                # Green "it landed" border flashed after the object arrives.
                send_overlay.flash(None, style=send_overlay.RECEIVE_STYLE)
            except Exception as exc:
                log.debug("receive animation failed: %s", exc)
        if self.config.auto_open:
            open_with_default_app(path)

    def on_pick(self):
        """Grab gesture started — lock the current image so moving the hand (or
        a changing foreground) doesn't swap what gets thrown."""
        self._held_image = self.last_image
        self._held_rect = self.last_image_rect
        if self._held_image:
            self.set_status(f"Picked up {self._held_image.name} — move & open hand",
                            (0, 220, 255), 8)
        else:
            self.set_status("Picked up (no image open)", (0, 180, 255))

    def on_cancel(self):
        self._held_image = None
        self._held_rect = None
        self.set_status("Released (cancelled)", (180, 180, 180), 3)

    def on_throw(self, direction: str):
        """A gesture fired in `direction`. Payload: the held image (grab mode)
        or the clipboard-first resolution chain (copied files -> Explorer
        selection -> open image -> copied screenshot -> last image)."""
        if self._held_image:
            image, src = self._held_image, "picked image"
        else:
            image, src = resolve_payload(self.last_image)
        rect = self._held_rect or self.last_image_rect
        self._held_image = None
        self._held_rect = None
        if image:
            log.info("payload: %s (%s)", image.name, src)
            self.set_status(f"Sending {src}: {image.name}", (0, 220, 255), 5)
        else:
            self.set_status(src.capitalize(), (0, 180, 255), 5)
        if self.config.use_picker():
            self._picker_swipe(direction, image, rect)
        else:
            self._do_throw(direction, image, rect)

    # NOTE: direction flows all the way to the peer so the receiver can play a
    # matching "flying in" animation from the sender's side.

    # ---- device picker (3+ devices) ---------------------------------
    def _picker_swipe(self, direction: str, image: Path | None, rect):
        """First swipe opens the picker; later swipes move the highlight. A
        dwell (no swipe for PICKER_CONFIRM_DELAY) confirms and sends."""
        with self._picker_lock:
            if not self._picker_active:
                devices = self.config.all_devices()
                if not devices:
                    self.set_status("No devices configured", (0, 180, 255))
                    return
                self._picker_devices = devices
                self._picker_image = image
                self._picker_rect = rect
                self._picker_index = 0 if direction == "right" else len(devices) - 1
                self._picker_active = True
                names = [d.name for d in devices]
                send_overlay.selector_show(
                    names, title="Throw to…",
                    style=self.config.send_animation_style)
                send_overlay.selector_set(self._picker_index)
                log.info("picker opened (%d devices)", len(devices))
            else:
                step = 1 if direction == "right" else -1
                self._picker_index = ((self._picker_index + step)
                                      % len(self._picker_devices))
                send_overlay.selector_set(self._picker_index)
            name = self._picker_devices[self._picker_index].name
            self.set_status(f"Pick: {name}  (swipe to move, hold to send)",
                            (0, 220, 255), 6)
            self._arm_picker_confirm()

    def _arm_picker_confirm(self):
        if self._picker_timer:
            self._picker_timer.cancel()
        self._picker_timer = threading.Timer(PICKER_CONFIRM_DELAY,
                                              self._confirm_pick)
        self._picker_timer.daemon = True
        self._picker_timer.start()

    def _confirm_pick(self):
        with self._picker_lock:
            if not self._picker_active:
                return
            device = self._picker_devices[self._picker_index]
            image, rect = self._picker_image, self._picker_rect
            self._picker_active = False
            self._picker_timer = None
        send_overlay.selector_hide()
        log.info("picker confirmed -> %s", device.name)
        self._throw_to_peer(device, image, rect, ">>", direction="right")

    # ---- canvas (map) gesture ---------------------------------------
    def _canvas_tick(self):
        """Per-frame: show the map when the open hand is up, highlight the device
        your hand points toward (angle from screen centre), and send after a
        short hold. The map is debounced — a brief hand loss won't close it."""
        det = self.detector
        targets = self._canvas_targets_override or self.config.positioned_targets()
        if not targets:
            if det and det.hand_present:
                self.set_status("Canvas: place devices on the map (GUI canvas)",
                                (0, 180, 255))
            return

        now = time.time()
        if not det.hand_present or not det.open_hand:
            self._canvas_miss += 1
            if self._canvas_miss > CANVAS_GRACE_FRAMES and self._canvas_shown:
                send_overlay.radar_hide()
                self._canvas_shown = False
                self._canvas_aim = -1
            return
        self._canvas_miss = 0

        if not self._canvas_shown:
            self._canvas_targets = targets
            labels = [((("\U0001f512 " + p.name) if self.config.is_blocked(p.name)
                        else p.name), p.x, p.y) for p in targets]
            send_overlay.radar_show(labels, "Reach toward a device")
            self._canvas_shown = True
            self._canvas_aim = -1

        px, py = det.palm_x, det.palm_y
        if px is None or py is None:
            return
        dx, dy = px - 0.5, py - 0.5
        if (dx * dx + dy * dy) ** 0.5 < CANVAS_AIM_MIN_MAG:
            if self._canvas_aim != -1:
                self._canvas_aim = -1
                send_overlay.radar_set(-1)
            self._canvas_since = now
            return

        hand_ang = math.atan2(dy, dx)
        best, best_diff = -1, math.pi
        for i, p in enumerate(self._canvas_targets):
            if self.config.is_blocked(p.name):
                continue  # strict list: can't even aim at a blocked peer
            t_ang = math.atan2(p.y - 0.5, p.x - 0.5)
            diff = abs(_angle_diff(hand_ang, t_ang))
            if diff < best_diff:
                best, best_diff = i, diff
        if best_diff > math.radians(CANVAS_MAX_ANGLE_DEG):
            best = -1

        if best != self._canvas_aim:
            self._canvas_aim = best
            self._canvas_since = now
            send_overlay.radar_set(best)
            return
        if best < 0 or now - self._canvas_last_fire < CANVAS_COOLDOWN:
            return
        if now - self._canvas_since >= CANVAS_DWELL:
            target = self._canvas_targets[best]
            self._canvas_last_fire = now
            self._canvas_aim = -1
            self._canvas_shown = False
            send_overlay.radar_hide()
            if self.config.is_blocked(target.name):
                log.info("canvas blocked -> %s (strict list)", target.name)
                self.set_status(f"Blocked (strict): {target.name}", (0, 0, 255), 4)
                return
            payload, src = resolve_payload(self.last_image)
            if self._canvas_dry_run:
                # Simulation: show what WOULD be sent, but don't send.
                what = f"{payload.name} ({src})" if payload else src
                log.info("canvas SIM -> would send %s to %s", what, target.name)
                self.set_status(f"Would send {what} → {target.name} (simulation)",
                                (0, 220, 0), 5)
                return
            if self._canvas_send_fn is not None:
                # Custom transport (e.g. IP Messenger).
                if not payload:
                    self.set_status(src.capitalize(), (0, 180, 255), 5)
                    return
                try:
                    self._canvas_send_fn(target, payload)
                    self.set_status(f"Sent {payload.name} ({src}) → "
                                    f"{target.name}", (0, 220, 0), 5)
                except Exception as exc:
                    log.error("canvas send_fn failed: %s", exc)
                    self.set_status(f"Send failed -> {target.name}: {exc}",
                                    (0, 0, 255), 6)
                return
            direction = "left" if target.x < 0.5 else "right"
            log.info("canvas send %s (%s) -> %s",
                     payload.name if payload else "(none)", src, target.name)
            self._throw_to_peer(target, payload, self.last_image_rect,
                                ">>", direction=direction)

    def _do_throw(self, direction: str, image: Path | None,
                  rect: tuple[int, int, int, int] | None = None):
        peer = self.config.peer_for(direction)
        arrow = "<<" if direction == "left" else ">>"
        if not peer:
            log.warning("throw %s but no peer in '%s' slot", direction, direction)
            self.set_status(f"{arrow} No peer in '{direction}' slot", (0, 180, 255))
            return
        self._throw_to_peer(peer, image, rect, arrow, direction=direction)

    def _throw_to_peer(self, peer, image: Path | None, rect, arrow: str = ">>",
                       direction: str = "right"):
        if self.config.is_blocked(getattr(peer, "name", "")):
            log.info("blocked send to %s (strict list)", peer.name)
            self.set_status(f"Blocked (strict): {peer.name}", (0, 0, 255), 5)
            return
        if not image:
            log.warning("throw to %s but no image to send", peer.name)
            self.set_status(f"{arrow} No image open to throw", (0, 180, 255))
            return

        # Redundant-send guard: same file to same peer within the window.
        now = time.time()
        window = self.config.resend_block_seconds
        self._recent_sends = {k: v for k, v in self._recent_sends.items()
                              if now - v < window}
        try:
            mtime = image.stat().st_mtime
        except OSError:
            mtime = 0.0
        key = (peer.url(), str(image), mtime)
        last = self._recent_sends.get(key)
        if last is not None:
            ago = now - last
            log.info("skip redundant send %s -> %s (sent %.1fs ago)",
                     image.name, peer.name, ago)
            self.set_status(f"{arrow} Already sent {image.name} to {peer.name} "
                            f"({ago:.0f}s ago)", (0, 180, 255))
            return
        self._recent_sends[key] = now

        log.info("throwing %s -> %s (%s)", image.name, peer.name, peer.url())
        self.set_status(f"{arrow} Throwing {image.name} to {peer.name}...",
                        (0, 220, 255), 10)
        if self.config.show_send_animation:
            try:
                send_overlay.flash(rect, style=self.config.send_animation_style)
                # The image itself flies off toward the throw direction.
                send_overlay.fly_out(image, direction)
            except Exception as exc:  # animation is cosmetic, never fatal
                log.debug("send animation failed: %s", exc)
        threading.Thread(
            target=self._send_worker, args=(peer, image, key, direction), daemon=True
        ).start()

    def _send_worker(self, peer, image: Path, key, direction: str = ""):
        try:
            result = transfer.send_file(peer.url(), image,
                                        self.config.device_name, direction=direction)
            log.info("SENT %s -> %s (%s)", image.name, peer.name, result)
            self.set_status(f"Sent {image.name} -> {peer.name}", (0, 220, 0), 6)
        except Exception as exc:  # network/file errors shown, not fatal
            self._recent_sends.pop(key, None)  # failed: allow immediate retry
            log.error("send failed %s -> %s: %s", image.name, peer.name, exc)
            self.set_status(f"Failed -> {peer.name}: {exc}", (0, 0, 255), 8)

    # ---- detector ----------------------------------------------------
    def _build_detector(self) -> BaseHandDetector:
        dist = self.config.swipe_min_distance
        mode = self._gesture_override or self.config.gesture_mode
        if mode == "grab":
            return GrabDetector(on_throw=self.on_throw, on_pick=self.on_pick,
                                on_cancel=self.on_cancel, min_distance=dist)
        if mode == "two":
            return TwoFingerDetector(on_swipe=self.on_throw, min_distance=dist)
        if mode == "palm":
            return PalmReachDetector(on_swipe=self.on_throw, min_distance=dist)
        if mode == "canvas":
            return CanvasDetector(min_distance=dist)
        # Default: whole-hand swipe (flick left/right).
        return SwipeDetector(on_swipe=self.on_throw, min_distance=dist)

    # ---- entry points ------------------------------------------------
    def run(self):
        """Foreground CLI: preview window on, q/ESC quits."""
        self._stop = threading.Event()
        self._preview_fn = lambda: True
        self._quit_on_key = True
        self._serve()
        self._camera_phase()

    def run_canvas_sim(self, targets, dry_run: bool = True, send_fn=None):
        """Hand-controlled canvas: reach toward a device on the map, hold to
        select. `dry_run` True = nothing is sent (simulation); otherwise
        `send_fn(target, image)` performs the real send. Preview on, q/ESC
        quits. Does not touch saved config or the normal send path."""
        self._gesture_override = "canvas"
        self._canvas_targets_override = list(targets)
        self._canvas_dry_run = dry_run
        self._canvas_send_fn = send_fn
        self._stop = threading.Event()
        self._preview_fn = lambda: True
        self._quit_on_key = True
        self._camera_phase()

    def start_background(self, stop_event: threading.Event,
                         preview_fn, on_preview_key, frame_sink=None):
        """Tray/daemon/GUI: shared stop Event, preview toggled on demand.

        If `frame_sink` is given, annotated frames are handed to it (the GUI
        draws them in-window) instead of opening an OpenCV window."""
        self._stop = stop_event
        self._preview_fn = preview_fn
        self._quit_on_key = False
        self._on_preview_key = on_preview_key
        self._frame_sink = frame_sink
        self._serve()
        self._camera_phase()

    def _serve(self):
        transfer.on_received = self.on_received
        transfer.on_receiving = lambda sender, name: self.set_status(
            f"Receiving {name} from {sender}…", (0, 200, 255), 30)
        transfer.start_receiver(self.config)
        log.info("receiving on %s:%d as '%s'",
                 local_ip(), self.config.listen_port, self.config.device_name)
        log.info("log file: %s", LOG_PATH)

    def _camera_phase(self):
        # Retry a few times: the camera can be briefly busy (just released by
        # another app or AirThrow window) and succeed a moment later.
        cap = None
        backend = ""
        for attempt in range(4):
            cap, backend = open_camera(self.camera_index)
            if cap is not None:
                break
            log.warning("camera %d: %s (attempt %d/4)",
                        self.camera_index, backend, attempt + 1)
            if self._stop.wait(0.7):
                return
        if cap is None:
            self.receive_only = True
            self._receive_until_stop()
            return
        self.receive_only = False
        self.detector = self._build_detector()
        log.info("camera %d open via %s; gesture=%s; sensitivity=%.2f",
                 self.camera_index, backend, self.config.gesture_mode,
                 self.config.swipe_min_distance)
        # Detect the foreground image on its own thread: psutil.open_files() is
        # slow on Windows (worse right after a window switch) and would stall the
        # camera loop, making the preview stutter/freeze. Off-thread keeps it smooth.
        fg = threading.Thread(target=self._foreground_worker,
                              name="airthrow-foreground", daemon=True)
        fg.start()
        try:
            self._camera_loop(cap)
        finally:
            if self._canvas_shown:
                send_overlay.radar_hide()
                self._canvas_shown = False
            if self.detector:
                self.detector.close()
            cap.release()
            cv2.destroyAllWindows()
            fg.join(timeout=1)
            log.info("stopped")

    def _foreground_worker(self):
        while not self._stop.is_set():
            try:
                self._refresh_foreground_image()
            except Exception as exc:
                log.debug("foreground poll failed: %s", exc)
            self._stop.wait(0.3)

    def _camera_loop(self, cap):
        frame_no = 0
        read_fails = 0
        window_open = False
        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok:
                read_fails += 1
                if read_fails in (1, 30) or read_fails % 150 == 0:
                    log.warning("no frame from camera %d (%d in a row)",
                                self.camera_index, read_fails)
                if read_fails > 300:
                    log.error("camera %d stopped delivering frames; receive-only",
                              self.camera_index)
                    cap.release()
                    cv2.destroyAllWindows()
                    self._receive_until_stop()
                    return
                cv2.waitKey(10)
                continue
            read_fails = 0
            frame = cv2.flip(frame, 1)  # mirror: feels natural
            self._update_fps()
            frame_no += 1

            self.camera_ok = True
            frame, _ = self.detector.process(frame)
            if (self._gesture_override or self.config.gesture_mode) == "canvas":
                self._canvas_tick()

            want_preview = self._preview_fn()

            # GUI path: hand annotated frames to the sink (drawn in-window).
            if self._frame_sink is not None:
                if want_preview:
                    self._draw_overlay(frame)
                    try:
                        self._frame_sink(frame)
                    except Exception:
                        pass
                continue

            # CLI/tray path: an OpenCV window.
            if want_preview:
                if not window_open:
                    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
                    try:
                        cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_TOPMOST, 1)
                    except cv2.error:
                        pass
                    window_open = True
                self._draw_overlay(frame)
                try:
                    cv2.imshow(WINDOW_NAME, frame)
                except cv2.error as exc:
                    log.error("cv2.imshow failed (no GUI backend?): %s", exc)
                    return
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    if self._quit_on_key:
                        self._stop.set()
                    else:
                        self._on_preview_key()  # tray: just hide the preview
            elif window_open:
                cv2.destroyWindow(WINDOW_NAME)
                window_open = False

    def _receive_until_stop(self):
        """No usable camera: keep accepting throws. Sending needs a webcam."""
        self.receive_only = True
        log.warning(
            "camera %d unavailable — RECEIVE-ONLY. Can accept throws but cannot "
            "send (gestures need a webcam).", self.camera_index)
        while not self._stop.is_set():
            self._stop.wait(0.5)

    # ---- helpers -----------------------------------------------------
    def _update_fps(self):
        now = time.time()
        if self._last_frame_t:
            dt = now - self._last_frame_t
            if dt > 0:
                self._fps = 0.9 * self._fps + 0.1 * (1.0 / dt)
        self._last_frame_t = now

    def _refresh_foreground_image(self):
        _, title = foreground_window()
        if WINDOW_NAME.lower() in (title or "").lower():
            return  # our own preview window; keep whatever we last saw
        img = current_image()
        if img:
            # Remember the window it's shown in so the send border frames it.
            rect = foreground_rect()
            if rect:
                self.last_image_rect = rect
            if img != self.last_image:
                log.debug("foreground image -> %s", img)
                self.last_image = img

    # ---- overlay -----------------------------------------------------
    def _draw_overlay(self, frame):
        h, w = frame.shape[:2]
        if self.config.gesture_mode == "palm":
            self._draw_palm_zones(frame)
        left = self.config.peer_for("left")
        right = self.config.peer_for("right")
        cv2.putText(frame, f"<< {left.name if left else '(none)'}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 200, 0), 2)
        rtext = f"{right.name if right else '(none)'} >>"
        (tw, _), _ = cv2.getTextSize(rtext, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        cv2.putText(frame, rtext, (w - tw - 10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 200, 0), 2)

        img_label = (f"Open: {self.last_image.name}" if self.last_image
                     else "Open: (no image)")
        cv2.putText(frame, img_label, (10, h - 45),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 2)

        if time.time() < self.status_until or self.status == "Ready":
            cv2.putText(frame, self.status, (10, h - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, self.status_color, 2)

        if self.debug:
            self._draw_debug(frame)

    def _draw_palm_zones(self, frame):
        """Shade the left/right 'hold your open hand here' zones and show the
        dwell progress as the zone fills in."""
        from .gestures import PALM_LEFT_ZONE, PALM_RIGHT_ZONE
        h, w = frame.shape[:2]
        lx = int(PALM_LEFT_ZONE * w)
        rx = int(PALM_RIGHT_ZONE * w)
        det = self.detector
        zone = getattr(det, "_zone", None)
        dwell = getattr(det, "dwell", 0.0)

        for side, (x0, x1) in (("left", (0, lx)), ("right", (rx, w))):
            active = zone == side
            fill = dwell if active else 0.0
            overlay = frame.copy()
            colour = (0, 220, 0) if fill >= 1.0 else (0, 180, 255)
            cv2.rectangle(overlay, (x0, 0), (x1, h), colour, -1)
            alpha = 0.12 + 0.33 * fill if active else 0.08
            cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)
            label = "HOLD OPEN HAND"
            cv2.putText(frame, label, (x0 + 10 if side == "left" else x1 - 220,
                                       h // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    def _draw_debug(self, frame):
        h, w = frame.shape[:2]
        det = self.detector
        hand = det.hand_present if det else False
        palm_x = det.palm_x if det else None
        cur_dx = det.cur_dx if det else 0.0
        grabbed = det.grabbed if det else False
        mode = self.config.gesture_mode

        state = "GRABBED" if grabbed else ("hand" if hand else "no hand")
        cv2.putText(frame,
                    f"FPS {self._fps:4.1f}  {mode}  {state}  fingers:{det.fingers if det else 0}",
                    (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 220, 0) if (hand if mode == 'swipe' else grabbed) else (0, 0, 220), 2)

        track_y = 85
        cv2.line(frame, (10, track_y), (w - 10, track_y), (90, 90, 90), 1)
        if palm_x is not None:
            px = int(10 + palm_x * (w - 20))
            cv2.circle(frame, (px, track_y), 6,
                       (0, 220, 0) if grabbed else (0, 220, 255), -1)

        bar_x, bar_y, bar_w = 10, 110, w - 20
        threshold = self.config.swipe_min_distance
        progress = min(abs(cur_dx) / threshold, 1.0) if cur_dx else 0.0
        fired = progress >= 1.0
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + 14), (70, 70, 70), 1)
        cv2.rectangle(frame, (bar_x, bar_y),
                      (bar_x + int(bar_w * progress), bar_y + 14),
                      (0, 220, 0) if fired else (0, 170, 220), -1)
        verb = "throw" if mode == "grab" else "swipe"
        arrow = "" if not cur_dx else ("-> right" if cur_dx > 0 else "<- left")
        cv2.putText(frame, f"{verb} {progress*100:3.0f}%  dx={cur_dx:+.2f} {arrow}",
                    (bar_x, bar_y + 32), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)


def probe_cameras(max_index: int = 4) -> None:
    """Report which camera indices actually deliver frames (diagnostic)."""
    print("Probing cameras (index 0..%d)..." % (max_index - 1))
    found = False
    for index in range(max_index):
        cap, backend = open_camera(index)
        if cap is not None:
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            print(f"  index {index}: OK via {backend} backend, {w}x{h}")
            cap.release()
            found = True
        else:
            print(f"  index {index}: {backend}")
    if found:
        print("\nUse a working index with:  airthrow run --camera <index>")
    else:
        print("\nNo camera delivered frames. Check it isn't used by another app, "
              "and (Linux) that you're in the 'video' group.")


def open_with_default_app(path: Path) -> None:
    """Open a file in the OS default application (Windows + Linux + macOS)."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform.startswith("darwin"):
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        log.info("opened %s", path)
    except Exception as exc:  # a failed open must never crash the receiver
        log.warning("could not auto-open %s: %s", path, exc)


def run(camera_index: int = 0, debug: bool = False):
    AirThrow(Config.load(), camera_index=camera_index, debug=debug).run()


@dataclass
class SimPeer:
    """A lightweight map target (name + canvas position + ip) for the canvas."""
    name: str
    x: float
    y: float
    ip: str = ""


def run_canvas_sim(targets, camera_index: int = 0, debug: bool = True,
                   dry_run: bool = True, send_fn=None):
    """Entry point: hand-controlled canvas over `targets` (iterable of SimPeer).
    dry_run True = simulation (no send); else send_fn(target, image) sends."""
    AirThrow(Config.load(), camera_index=camera_index, debug=debug).run_canvas_sim(
        targets, dry_run=dry_run, send_fn=send_fn)


def run_daemon(camera_index: int = 0):
    """Headless background run: receiver + gestures, no preview, no tray.
    Stops cleanly on SIGINT/SIGTERM (so a service manager can stop it)."""
    import signal

    app = AirThrow(Config.load(), camera_index=camera_index)
    stop = threading.Event()

    def _handle(*_):
        stop.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handle)
            except (ValueError, OSError):
                pass  # not on the main thread / unsupported

    log.info("daemon starting (headless background)")
    app.start_background(stop, lambda: False, lambda: None)
    log.info("daemon stopped")
