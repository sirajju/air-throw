"""Orchestrator: wire the camera + gestures + foreground image + transfer.

Run loop:
  * receiver runs in a background thread (so this PC can also *receive*)
  * webcam frames feed the swipe detector
  * a few times per second we remember the image open in the foreground window
    (ignoring AirThrow's own preview window)
  * a detected swipe grabs that remembered image and POSTs it to the peer
    assigned to that direction, on a worker thread so the camera never stalls

Pass debug=True (CLI: `run --debug`) for verbose console logs and an on-screen
diagnostic overlay (hand status, palm position, live swipe-progress bar, FPS).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2

from . import transfer
from .config import Config, local_ip
from .foreground_image import current_image, foreground_window
from .gestures import SWIPE_MIN_DISTANCE, SwipeDetector
from .logs import LOG_PATH, get_logger

log = get_logger()

WINDOW_NAME = "AirThrow"
POLL_EVERY = 8  # frames between foreground-image checks (~3-4x/sec)


class AirThrow:
    def __init__(self, config: Config, camera_index: int = 0, debug: bool = False):
        self.config = config
        self.camera_index = camera_index
        self.debug = debug
        self.last_image: Path | None = None
        self.status = "Ready"
        self.status_color = (255, 255, 255)
        self.status_until = 0.0
        self.detector: SwipeDetector | None = None
        self._fps = 0.0
        self._last_frame_t = 0.0

    # ---- status helpers ---------------------------------------------
    def set_status(self, text: str, color=(255, 255, 255), seconds: float = 4.0):
        self.status = text
        self.status_color = color
        self.status_until = time.time() + seconds

    # ---- events ------------------------------------------------------
    def on_received(self, sender: str, filename: str, path: Path):
        self.set_status(f"Received {filename} from {sender}", (0, 220, 0), 6)

    def on_swipe(self, direction: str):
        peer = self.config.peer_for(direction)
        arrow = "<<" if direction == "left" else ">>"
        if not peer:
            log.warning("swipe %s but no peer in '%s' slot", direction, direction)
            self.set_status(f"{arrow} No peer in '{direction}' slot", (0, 180, 255))
            return
        if not self.last_image:
            log.warning("swipe %s to %s but no image open to throw", direction, peer.name)
            self.set_status(f"{arrow} No image open to throw", (0, 180, 255))
            return
        image = self.last_image
        log.info("throwing %s -> %s (%s)", image.name, peer.name, peer.url())
        self.set_status(f"{arrow} Throwing {image.name} to {peer.name}...", (0, 220, 255), 10)
        threading.Thread(
            target=self._send_worker, args=(peer, image), daemon=True
        ).start()

    def _send_worker(self, peer, image: Path):
        try:
            result = transfer.send_file(peer.url(), image, self.config.device_name)
            log.info("SENT %s -> %s (%s)", image.name, peer.name, result)
            self.set_status(f"Sent {image.name} -> {peer.name}", (0, 220, 0), 6)
        except Exception as exc:  # network/file errors shown, not fatal
            log.error("send failed %s -> %s: %s", image.name, peer.name, exc)
            self.set_status(f"Failed -> {peer.name}: {exc}", (0, 0, 255), 8)

    # ---- main loop ---------------------------------------------------
    def run(self):
        transfer.on_received = self.on_received
        transfer.start_receiver(self.config)
        log.info("receiving on %s:%d as '%s'",
                 local_ip(), self.config.listen_port, self.config.device_name)
        log.info("log file: %s", LOG_PATH)

        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            cap.release()
            self._run_receive_only()
            return

        self.detector = SwipeDetector(on_swipe=self.on_swipe)
        log.info("camera %d open; swipe left/right to throw. Press q/ESC to quit.",
                 self.camera_index)
        frame_no = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    continue
                frame = cv2.flip(frame, 1)  # mirror: feels natural
                self._update_fps()

                # Remember the foreground image a few times per second.
                frame_no += 1
                if frame_no % POLL_EVERY == 0:
                    self._refresh_foreground_image()

                frame, _ = self.detector.process(frame)
                self._draw_overlay(frame)
                cv2.imshow(WINDOW_NAME, frame)

                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):  # ESC or q
                    break
        finally:
            self.detector.close()
            cap.release()
            cv2.destroyAllWindows()
            log.info("stopped")

    def _run_receive_only(self):
        """No usable camera: stay alive as a receiver so this machine can still
        accept throws. Sending needs a webcam."""
        log.warning(
            "no camera on index %d — RECEIVE-ONLY mode. This machine can accept "
            "throws but cannot send (gestures need a webcam). Ctrl+C to quit.",
            self.camera_index,
        )
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    def _update_fps(self):
        now = time.time()
        if self._last_frame_t:
            dt = now - self._last_frame_t
            if dt > 0:
                # Exponential smoothing so the number doesn't jitter.
                self._fps = 0.9 * self._fps + 0.1 * (1.0 / dt)
        self._last_frame_t = now

    def _refresh_foreground_image(self):
        _, title = foreground_window()
        if WINDOW_NAME.lower() in (title or "").lower():
            return  # our own preview window; keep whatever we last saw
        img = current_image()
        if img and img != self.last_image:
            log.debug("foreground image -> %s", img)
            self.last_image = img

    # ---- overlay -----------------------------------------------------
    def _draw_overlay(self, frame):
        h, w = frame.shape[:2]
        left = self.config.peer_for("left")
        right = self.config.peer_for("right")
        cv2.putText(frame, f"<< {left.name if left else '(none)'}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 200, 0), 2)
        rtext = f"{right.name if right else '(none)'} >>"
        (tw, _), _ = cv2.getTextSize(rtext, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        cv2.putText(frame, rtext, (w - tw - 10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 200, 0), 2)

        img_label = f"Open: {self.last_image.name}" if self.last_image else "Open: (no image)"
        cv2.putText(frame, img_label, (10, h - 45),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 2)

        if time.time() < self.status_until or self.status == "Ready":
            cv2.putText(frame, self.status, (10, h - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, self.status_color, 2)

        if self.debug:
            self._draw_debug(frame)

    def _draw_debug(self, frame):
        """Diagnostic overlay: hand status, palm marker, and a live progress bar
        showing how close the current motion is to firing a swipe."""
        h, w = frame.shape[:2]
        det = self.detector
        hand = det.hand_present if det else False
        palm_x = det.palm_x if det else None
        cur_dx = det.cur_dx if det else 0.0

        cv2.putText(frame, f"FPS {self._fps:4.1f}  Hand {'YES' if hand else 'no '}",
                    (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 220, 0) if hand else (0, 0, 220), 2)

        # Palm position marker along a horizontal track.
        track_y = 85
        cv2.line(frame, (10, track_y), (w - 10, track_y), (90, 90, 90), 1)
        if palm_x is not None:
            px = int(10 + palm_x * (w - 20))
            cv2.circle(frame, (px, track_y), 6, (0, 220, 255), -1)

        # Swipe-progress bar: |cur_dx| vs the threshold needed to fire.
        bar_x, bar_y, bar_w = 10, 110, w - 20
        progress = min(abs(cur_dx) / SWIPE_MIN_DISTANCE, 1.0) if cur_dx else 0.0
        fired = progress >= 1.0
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + 14), (70, 70, 70), 1)
        cv2.rectangle(frame, (bar_x, bar_y),
                      (bar_x + int(bar_w * progress), bar_y + 14),
                      (0, 220, 0) if fired else (0, 170, 220), -1)
        arrow = "" if not cur_dx else ("-> right" if cur_dx > 0 else "<- left")
        cv2.putText(frame, f"swipe {progress*100:3.0f}%  dx={cur_dx:+.2f} {arrow}",
                    (bar_x, bar_y + 32), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)


def run(camera_index: int = 0, debug: bool = False):
    AirThrow(Config.load(), camera_index=camera_index, debug=debug).run()
