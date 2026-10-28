"""Hand-gesture detection: swipe left / swipe right via MediaPipe Hands.

We track the horizontal position of the palm across frames. A fast horizontal
sweep that covers enough of the frame within a short time window counts as a
"throw" in that direction. A cooldown prevents one physical swipe from firing
repeatedly.

The detector is a generator: feed it the webcam, it yields annotated frames and
calls your callback whenever a throw is recognised.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import cv2
import mediapipe as mp

# Tunables — these feel right for a webcam at arm's length; adjust to taste.
SWIPE_MIN_DISTANCE = 0.33   # fraction of frame width the palm must travel
SWIPE_MAX_DURATION = 0.6    # seconds; the travel must happen at least this fast
HISTORY_SECONDS = 0.8       # how much motion history to keep
COOLDOWN_SECONDS = 1.5      # ignore new swipes for this long after one fires


@dataclass
class _Sample:
    t: float
    x: float  # palm x, normalised 0..1 (already un-mirrored to screen space)


class SwipeDetector:
    def __init__(self, on_swipe: Callable[[str], None]):
        self.on_swipe = on_swipe
        self.history: deque[_Sample] = deque()
        self.last_fire = 0.0
        self._hands = mp.solutions.hands.Hands(
            max_num_hands=1,
            model_complexity=0,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.5,
        )
        self._draw = mp.solutions.drawing_utils
        self._conn = mp.solutions.hands.HAND_CONNECTIONS

    def close(self) -> None:
        self._hands.close()

    def process(self, frame_bgr):
        """Process one BGR frame (already mirrored for display). Returns the
        same frame with hand landmarks drawn and recent direction annotated."""
        now = time.time()
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        result = self._hands.process(rgb)

        direction = None
        if result.multi_hand_landmarks:
            hand = result.multi_hand_landmarks[0]
            self._draw.draw_landmarks(frame_bgr, hand, self._conn)
            # Landmark 9 = base of middle finger ≈ palm centre. x is already in
            # the mirrored display's coordinate space.
            palm_x = hand.landmark[9].x
            self.history.append(_Sample(now, palm_x))
            direction = self._detect(now)
        else:
            self.history.clear()

        return frame_bgr, direction

    def _detect(self, now: float) -> str | None:
        # Drop stale samples.
        while self.history and now - self.history[0].t > HISTORY_SECONDS:
            self.history.popleft()
        if len(self.history) < 3:
            return None
        if now - self.last_fire < COOLDOWN_SECONDS:
            return None

        # Look at the span from the oldest kept sample to the newest.
        start, end = self.history[0], self.history[-1]
        dx = end.x - start.x
        dt = end.t - start.t
        if abs(dx) >= SWIPE_MIN_DISTANCE and 0 < dt <= SWIPE_MAX_DURATION:
            direction = "right" if dx > 0 else "left"
            self.last_fire = now
            self.history.clear()
            self.on_swipe(direction)
            return direction
        return None
