"""Hand-gesture detection via MediaPipe Hands (Tasks API / HandLandmarker).

Two gesture modes share one hand tracker (BaseHandDetector):

* SwipeDetector — flick the palm left/right. Simple; fires on fast horizontal
  motion regardless of hand pose.
* GrabDetector — Huawei-style "pick and throw": make a fist to pick up the
  current image, move your hand toward the target, then open your hand to
  release/send. Far fewer accidental sends because it needs a deliberate
  grab + release.

The legacy `mediapipe.solutions` API was removed in MediaPipe 1.x, so we use
the Tasks API; the model file ships in airthrow/assets.
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
SWIPE_MIN_DISTANCE = 0.22   # fraction of frame width the palm must travel
SWIPE_MAX_DURATION = 0.6    # seconds; swipe travel must happen at least this fast
HISTORY_SECONDS = 0.8       # how much motion history to keep (swipe)
COOLDOWN_SECONDS = 1.5      # ignore new gestures for this long after one fires

GRAB_FRAMES = 3             # consecutive fist frames to "pick up"
RELEASE_FRAMES = 2          # consecutive open-hand frames to "release"

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


# --------------------------------------------------------------------------
# Finger-pose helpers (orientation-robust: radial distance from the wrist)
# --------------------------------------------------------------------------
def _dist(a, b) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def extended_finger_count(lm) -> int:
    """How many of index/middle/ring/pinky are extended. A finger is extended
    when its tip is farther from the wrist than its middle (pip) joint — this
    holds whatever way the hand is rotated, unlike comparing y-coordinates."""
    count = 0
    for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
        if _dist(lm[tip], lm[0]) > _dist(lm[pip], lm[0]):
            count += 1
    return count


def finger_states(lm) -> list[bool]:
    """Extended/not for [index, middle, ring, pinky] (thumb ignored)."""
    states = []
    for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
        states.append(_dist(lm[tip], lm[0]) > _dist(lm[pip], lm[0]))
    return states


def is_fist(lm) -> bool:
    return extended_finger_count(lm) <= 1


def is_open_hand(lm) -> bool:
    return extended_finger_count(lm) >= 3


def is_two_finger(lm) -> bool:
    """The 'peace' / V sign: index + middle up, and not a full open hand.

    Tolerant on ring/pinky (detection is noisy there): accept as long as index
    and middle are up and at least one of ring/pinky is down, so an open hand
    (all four up) is still rejected."""
    index, middle, ring, pinky = finger_states(lm)
    return index and middle and (not ring or not pinky)


# --------------------------------------------------------------------------
# Shared hand tracker
# --------------------------------------------------------------------------
class BaseHandDetector:
    def __init__(self, min_distance: float = SWIPE_MIN_DISTANCE):
        self.min_distance = min_distance
        self._start = time.time()
        self._last_ts = -1  # ms; detect_for_video needs strictly-increasing ts

        # Live telemetry for the debug overlay / logs (updated each frame).
        self.hand_present = False
        self.palm_x: float | None = None
        self.cur_dx = 0.0
        self.cur_dt = 0.0
        self.grabbed = False      # set by GrabDetector; always False for swipe
        self.fingers = 0

        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(
                model_asset_path=asset_path("hand_landmarker.task")
            ),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
            # Lower thresholds so a hand already in a fist / V-sign is picked up
            # directly, without first showing an open palm to acquire tracking.
            min_hand_detection_confidence=0.35,
            min_hand_presence_confidence=0.35,
            min_tracking_confidence=0.35,
        )
        self._landmarker = vision.HandLandmarker.create_from_options(options)

    def close(self) -> None:
        self._landmarker.close()

    def process(self, frame_bgr):
        """Process one BGR frame (already mirrored). Returns (frame, direction)
        with the hand skeleton drawn on."""
        now = time.time()
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        ts = int((now - self._start) * 1000)
        if ts <= self._last_ts:
            ts = self._last_ts + 1
        self._last_ts = ts

        result = self._landmarker.detect_for_video(mp_image, ts)

        direction = None
        if result.hand_landmarks:
            lm = result.hand_landmarks[0]
            self.hand_present = True
            self.palm_x = lm[9].x
            self.fingers = extended_finger_count(lm)
            direction = self._update(lm, now)
            colour = (0, 220, 0) if self.grabbed else (0, 220, 255)
            _draw_hand(frame_bgr, lm, colour)
        else:
            self.hand_present = False
            self.palm_x = None
            self.cur_dx = 0.0
            self.cur_dt = 0.0
            self.fingers = 0
            self._reset()

        return frame_bgr, direction

    # Subclasses implement the gesture logic.
    def _update(self, lm, now: float) -> str | None:
        raise NotImplementedError

    def _reset(self) -> None:
        pass


# --------------------------------------------------------------------------
# Swipe gesture
# --------------------------------------------------------------------------
class SwipeDetector(BaseHandDetector):
    def __init__(self, on_swipe: Callable[[str], None],
                 min_distance: float = SWIPE_MIN_DISTANCE):
        super().__init__(min_distance)
        self.on_swipe = on_swipe
        self.history: deque[_Sample] = deque()
        self.last_fire = 0.0

    def _reset(self) -> None:
        self.history.clear()

    def _update(self, lm, now: float) -> str | None:
        self.history.append(_Sample(now, lm[9].x))

        while self.history and now - self.history[0].t > HISTORY_SECONDS:
            self.history.popleft()
        if len(self.history) < 3:
            self.cur_dx = 0.0
            self.cur_dt = 0.0
            return None

        start, end = self.history[0], self.history[-1]
        dx = end.x - start.x
        dt = end.t - start.t
        self.cur_dx = dx
        self.cur_dt = dt

        in_cooldown = now - self.last_fire < COOLDOWN_SECONDS
        if (not in_cooldown and abs(dx) >= self.min_distance
                and 0 < dt <= SWIPE_MAX_DURATION):
            direction = "right" if dx > 0 else "left"
            self.last_fire = now
            self.history.clear()
            log.info("SWIPE %s (dx=%+.2f over %.0fms)", direction, dx, dt * 1000)
            self.on_swipe(direction)
            return direction

        if (not in_cooldown and 0 < dt <= SWIPE_MAX_DURATION
                and abs(dx) >= self.min_distance * 0.6):
            log.debug("near-miss dx=%+.2f (need %.2f) dt=%.0fms",
                      dx, self.min_distance, dt * 1000)
        return None


# --------------------------------------------------------------------------
# Two-finger swipe gesture (hold index+middle up, then flick)
# --------------------------------------------------------------------------
class TwoFingerDetector(SwipeDetector):
    """A swipe that only counts while the hand shows the two-finger (V) pose.

    Requiring index+middle up (ring+pinky down) throughout the motion filters
    out accidental hand movement, while staying as quick as a plain swipe.
    """

    POSE_GRACE_FRAMES = 4  # tolerate this many non-pose frames mid-swipe

    def __init__(self, on_swipe: Callable[[str], None],
                 min_distance: float = SWIPE_MIN_DISTANCE):
        super().__init__(on_swipe, min_distance)
        self.pose_ok = False
        self._pose_miss = 0

    def _reset(self) -> None:
        super()._reset()
        self.pose_ok = False
        self._pose_miss = 0

    def _update(self, lm, now: float) -> str | None:
        if is_two_finger(lm):
            self._pose_miss = 0
            self.pose_ok = True
            return super()._update(lm, now)

        # Not currently a clean V-sign. Tolerate brief flicker so detection
        # noise doesn't keep resetting an in-progress swipe.
        self._pose_miss += 1
        if self._pose_miss <= self.POSE_GRACE_FRAMES and self.history:
            self.pose_ok = True
            return super()._update(lm, now)

        self.pose_ok = False
        self.history.clear()
        self.cur_dx = 0.0
        self.cur_dt = 0.0
        return None


# --------------------------------------------------------------------------
# Grab-and-throw gesture (Huawei-style)
# --------------------------------------------------------------------------
class GrabDetector(BaseHandDetector):
    """Fist = pick up; move; open hand = release toward a direction."""

    def __init__(self, on_throw: Callable[[str], None],
                 on_pick: Callable[[], None] | None = None,
                 on_cancel: Callable[[], None] | None = None,
                 min_distance: float = SWIPE_MIN_DISTANCE):
        super().__init__(min_distance)
        self.on_throw = on_throw
        self.on_pick = on_pick or (lambda: None)
        self.on_cancel = on_cancel or (lambda: None)
        self.last_fire = 0.0
        self._grab_x = 0.0
        self._fist_frames = 0
        self._open_frames = 0

    def _reset(self) -> None:
        # Hand lost: cancel an in-progress grab.
        if self.grabbed:
            log.info("grab cancelled (hand lost)")
            self.on_cancel()
        self.grabbed = False
        self._fist_frames = 0
        self._open_frames = 0

    def _update(self, lm, now: float) -> str | None:
        fist = is_fist(lm)
        openh = is_open_hand(lm)
        palm_x = lm[9].x

        if not self.grabbed:
            if now - self.last_fire < COOLDOWN_SECONDS:
                return None
            self._fist_frames = self._fist_frames + 1 if fist else 0
            if self._fist_frames >= GRAB_FRAMES:
                self.grabbed = True
                self._grab_x = palm_x
                self._open_frames = 0
                self.cur_dx = 0.0
                log.info("GRAB (picked up)")
                self.on_pick()
            return None

        # Grabbed: track travel since the grab point.
        self.cur_dx = palm_x - self._grab_x
        self._open_frames = self._open_frames + 1 if openh else 0
        if self._open_frames >= RELEASE_FRAMES:
            dx = self.cur_dx
            self.grabbed = False
            self._fist_frames = 0
            self.last_fire = now
            if abs(dx) >= self.min_distance:
                direction = "right" if dx > 0 else "left"
                log.info("THROW %s (released, dx=%+.2f)", direction, dx)
                self.on_throw(direction)
                return direction
            log.info("released without enough travel (dx=%+.2f) — cancelled", dx)
            self.on_cancel()
        return None


def _draw_hand(frame, landmarks, colour=(0, 220, 255)) -> None:
    """Draw the 21-point hand skeleton onto the BGR frame."""
    h, w = frame.shape[:2]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (255, 255, 255), 2)
    for (x, y) in pts:
        cv2.circle(frame, (x, y), 4, colour, -1)
