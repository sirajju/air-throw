"""Hand-gesture detection: swipe left / swipe right via MediaPipe Hands.

Uses MediaPipe's Tasks API (HandLandmarker) — the legacy `mediapipe.solutions`
API was removed in MediaPipe 1.x. The model file ships in airthrow/assets.

We track the horizontal position of the palm across frames. A fast horizontal
sweep that covers enough of the frame within a short time window counts as a
"throw" in that direction. A cooldown prevents one physical swipe from firing
repeatedly. The detector draws the hand skeleton onto each frame for feedback.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from . import asset_path
from .logs import get_logger

log = get_logger()

# Tunables — these feel right for a webcam at arm's length; adjust to taste.
# SWIPE_MIN_DISTANCE is the default; it can be overridden per-device via config
# (set-sensitivity) and passed into SwipeDetector.
SWIPE_MIN_DISTANCE = 0.22   # fraction of frame width the palm must travel
SWIPE_MAX_DURATION = 0.6    # seconds; the travel must happen at least this fast
HISTORY_SECONDS = 0.8       # how much motion history to keep
COOLDOWN_SECONDS = 1.5      # ignore new swipes for this long after one fires

# 21-point hand skeleton edges (same topology as MediaPipe's HAND_CONNECTIONS).
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),           # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),           # index
    (5, 9), (9, 10), (10, 11), (11, 12),      # middle
    (9, 13), (13, 14), (14, 15), (15, 16),    # ring
    (13, 17), (17, 18), (18, 19), (19, 20),   # pinky
    (0, 17),                                  # palm base
]


@dataclass
class _Sample:
    t: float
    x: float  # palm x, normalised 0..1 (in the mirrored display's space)


class SwipeDetector:
    def __init__(self, on_swipe: Callable[[str], None],
                 min_distance: float = SWIPE_MIN_DISTANCE):
        self.on_swipe = on_swipe
        self.min_distance = min_distance
        self.history: deque[_Sample] = deque()
        self.last_fire = 0.0
        self._start = time.time()
        self._last_ts = -1  # ms; detect_for_video needs strictly-increasing ts

        # Live telemetry for the debug overlay / logs (updated each frame).
        self.hand_present = False
        self.palm_x: float | None = None
        self.cur_dx = 0.0   # signed travel across the current history window
        self.cur_dt = 0.0   # seconds spanned by that window

        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(
                model_asset_path=asset_path("hand_landmarker.task")
            ),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._landmarker = vision.HandLandmarker.create_from_options(options)

    def close(self) -> None:
        self._landmarker.close()

    def process(self, frame_bgr):
        """Process one BGR frame (already mirrored for display). Returns the
        same frame with hand landmarks drawn and the detected direction (or
        None)."""
        now = time.time()
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        ts = int((now - self._start) * 1000)
        if ts <= self._last_ts:  # guarantee strictly increasing timestamps
            ts = self._last_ts + 1
        self._last_ts = ts

        result = self._landmarker.detect_for_video(mp_image, ts)

        direction = None
        if result.hand_landmarks:
            landmarks = result.hand_landmarks[0]
            _draw_hand(frame_bgr, landmarks)
            # Landmark 9 = base of middle finger ≈ palm centre.
            palm_x = landmarks[9].x
            self.hand_present = True
            self.palm_x = palm_x
            self.history.append(_Sample(now, palm_x))
            direction = self._detect(now)
        else:
            self.hand_present = False
            self.palm_x = None
            self.cur_dx = 0.0
            self.cur_dt = 0.0
            self.history.clear()

        return frame_bgr, direction

    def _detect(self, now: float) -> str | None:
        # Drop stale samples.
        while self.history and now - self.history[0].t > HISTORY_SECONDS:
            self.history.popleft()
        if len(self.history) < 3:
            self.cur_dx = 0.0
            self.cur_dt = 0.0
            return None

        start, end = self.history[0], self.history[-1]
        dx = end.x - start.x
        dt = end.t - start.t
        self.cur_dx = dx  # exposed for the overlay/log every frame
        self.cur_dt = dt

        in_cooldown = now - self.last_fire < COOLDOWN_SECONDS
        if (not in_cooldown
                and abs(dx) >= self.min_distance
                and 0 < dt <= SWIPE_MAX_DURATION):
            direction = "right" if dx > 0 else "left"
            self.last_fire = now
            self.history.clear()
            log.info("SWIPE %s (dx=%+.2f over %.0fms)", direction, dx, dt * 1000)
            self.on_swipe(direction)
            return direction

        # Near-miss diagnostics: a fast-enough swipe that fell just short.
        if (not in_cooldown and 0 < dt <= SWIPE_MAX_DURATION
                and abs(dx) >= self.min_distance * 0.6):
            log.debug("near-miss dx=%+.2f (need %.2f) dt=%.0fms",
                      dx, self.min_distance, dt * 1000)
        return None


def _draw_hand(frame, landmarks) -> None:
    """Draw the 21-point hand skeleton onto the BGR frame."""
    h, w = frame.shape[:2]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (255, 255, 255), 2)
    for (x, y) in pts:
        cv2.circle(frame, (x, y), 4, (0, 220, 255), -1)
