"""Orchestrator: wire the camera + gestures + foreground image + transfer.

Run loop:
  * receiver runs in a background thread (so this PC can also *receive*)
  * webcam frames feed the swipe detector
  * a few times per second we remember the image open in the foreground window
    (ignoring AirThrow's own preview window)
  * a detected swipe grabs that remembered image and POSTs it to the peer
    assigned to that direction, on a worker thread so the camera never stalls
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2

from . import transfer
from .config import Config, local_ip
from .foreground_image import current_image, foreground_window
from .gestures import SwipeDetector

WINDOW_NAME = "AirThrow"
POLL_EVERY = 8  # frames between foreground-image checks (~3-4x/sec)


class AirThrow:
    def __init__(self, config: Config, camera_index: int = 0):
        self.config = config
        self.camera_index = camera_index
        self.last_image: Path | None = None
        self.status = "Ready"
        self.status_color = (255, 255, 255)
        self.status_until = 0.0

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
            self.set_status(f"{arrow} No peer in '{direction}' slot", (0, 180, 255))
            return
        if not self.last_image:
            self.set_status(f"{arrow} No image open to throw", (0, 180, 255))
            return
        image = self.last_image
        self.set_status(f"{arrow} Throwing {image.name} to {peer.name}...", (0, 220, 255), 10)
        threading.Thread(
            target=self._send_worker, args=(peer, image), daemon=True
        ).start()

    def _send_worker(self, peer, image: Path):
        try:
            transfer.send_file(peer.url(), image, self.config.device_name)
            self.set_status(f"Sent {image.name} -> {peer.name}", (0, 220, 0), 6)
        except Exception as exc:  # network/file errors shown, not fatal
            self.set_status(f"Failed -> {peer.name}: {exc}", (0, 0, 255), 8)

    # ---- main loop ---------------------------------------------------
    def run(self):
        transfer.on_received = self.on_received
        transfer.start_receiver(self.config)
        print(f"[AirThrow] receiving on {local_ip()}:{self.config.listen_port} "
              f"as '{self.config.device_name}'")

        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            raise RuntimeError(
                f"Could not open camera index {self.camera_index}. "
                "Close other apps using it, or pass a different --camera."
            )

        detector = SwipeDetector(on_swipe=self.on_swipe)
        frame_no = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    continue
                frame = cv2.flip(frame, 1)  # mirror: feels natural

                # Remember the foreground image a few times per second.
                frame_no += 1
                if frame_no % POLL_EVERY == 0:
                    self._refresh_foreground_image()

                frame, _ = detector.process(frame)
                self._draw_overlay(frame)
                cv2.imshow(WINDOW_NAME, frame)

                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):  # ESC or q
                    break
        finally:
            detector.close()
            cap.release()
            cv2.destroyAllWindows()

    def _refresh_foreground_image(self):
        _, title = foreground_window()
        if WINDOW_NAME.lower() in (title or "").lower():
            return  # our own preview window; keep whatever we last saw
        img = current_image()
        if img:
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


def run(camera_index: int = 0):
    AirThrow(Config.load(), camera_index=camera_index).run()
