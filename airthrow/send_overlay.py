"""Huawei-style "throwing" animation: an animated gradient border that flashes
around the item being sent.

When a throw fires we briefly frame the source window (the image viewer) with a
glowing, flowing gradient border, then fade it out — visual confirmation that
AirThrow grabbed *that* window and flung it.

Cross-platform by construction: instead of one transparent hollow window (which
needs per-OS compositing tricks), we draw the border as **four thin always-on-top
strips** (top/bottom/left/right). Each strip is an opaque little Tk window
showing a slice of a seamless gradient that scrolls around the frame, so it
works the same on Windows and Linux with no transparency support required.

All Tk access lives on ONE dedicated thread (Tkinter is not thread-safe); the
rest of the app just calls :func:`flash` from any thread.
"""

from __future__ import annotations

import queue
import sys
import threading
import time

from .logs import get_logger

log = get_logger()

THICKNESS = 9          # border strip thickness, px
PERIOD = 360           # gradient repeat length, px (seamless loop)
SCROLL_PX_PER_S = 320  # how fast the gradient flows around the border
DEFAULT_DURATION = 1.3  # seconds the border stays up
FADE_SECONDS = 0.45     # of that, the trailing fade-out
FRAME_MS = 33           # ~30 fps

# Seamless gradient stops (teal -> blue -> violet -> pink -> teal). The last
# stop equals the first so the scroll wraps without a visible seam.
_STOPS = [
    (0, 229, 200),    # teal
    (33, 150, 243),   # blue
    (124, 77, 255),   # violet
    (255, 77, 157),   # pink
    (0, 229, 200),    # back to teal
]


def _build_gradient():
    """A (PERIOD, 3) uint8 numpy array cycling smoothly through the stops."""
    import numpy as np

    seg = PERIOD / (len(_STOPS) - 1)
    out = np.zeros((PERIOD, 3), dtype=np.uint8)
    for i in range(PERIOD):
        pos = i / seg
        a = int(pos)
        f = pos - a
        c0 = _STOPS[a]
        c1 = _STOPS[min(a + 1, len(_STOPS) - 1)]
        out[i] = [int(c0[k] + (c1[k] - c0[k]) * f) for k in range(3)]
    return out


class _Flash:
    """One border animation around a rectangle, living on the overlay thread."""

    def __init__(self, manager, root, gradient, rect, duration):
        self._mgr = manager
        self._root = root
        self._grad = gradient
        self._duration = duration
        self._start = time.time()
        self._strips = []  # (toplevel, label, orientation, length)

        import tkinter as tk
        from PIL import Image, ImageTk  # noqa: F401 (import proves availability)

        if rect is None:
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            x, y, w, h = 4, 4, sw - 8, sh - 8
        else:
            x, y, w, h = rect
        self._tk = tk
        self._ImageTk = ImageTk
        self._Image = Image

        t = THICKNESS
        # (geometry string, orientation) for top, bottom, left, right.
        specs = [
            (f"{w}x{t}+{x}+{y}", "h", w),
            (f"{w}x{t}+{x}+{y + h - t}", "h", w),
            (f"{t}x{h}+{x}+{y}", "v", h),
            (f"{t}x{h}+{x + w - t}+{y}", "v", h),
        ]
        for geom, orient, length in specs:
            win = tk.Toplevel(root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            try:
                win.attributes("-alpha", 1.0)
            except tk.TclError:
                pass
            win.geometry(geom)
            lbl = tk.Label(win, bd=0, highlightthickness=0, bg="#000000")
            lbl.pack(fill="both", expand=True)
            _make_click_through(win)
            self._strips.append([win, lbl, orient, max(1, length)])

        self._tick()

    def _slice_image(self, orient, length, offset):
        """A PhotoImage: a THICKNESS-wide slice of the scrolling gradient."""
        import numpy as np

        idx = (np.arange(length) + offset) % PERIOD
        line = self._grad[idx]  # (length, 3)
        if orient == "h":
            arr = np.broadcast_to(line[None, :, :], (THICKNESS, length, 3))
        else:
            arr = np.broadcast_to(line[:, None, :], (length, THICKNESS, 3))
        img = self._Image.fromarray(np.ascontiguousarray(arr), "RGB")
        return self._ImageTk.PhotoImage(img)

    def _tick(self):
        now = time.time()
        elapsed = now - self._start
        if elapsed >= self._duration:
            self._destroy()
            return

        offset = int(elapsed * SCROLL_PX_PER_S) % PERIOD
        remaining = self._duration - elapsed
        alpha = 1.0 if remaining > FADE_SECONDS else max(0.0, remaining / FADE_SECONDS)

        for strip in self._strips:
            win, lbl, orient, length = strip
            photo = self._slice_image(orient, length, offset)
            lbl.configure(image=photo)
            lbl.image = photo  # keep a reference so it isn't GC'd
            try:
                win.attributes("-alpha", alpha)
            except self._tk.TclError:
                pass

        self._root.after(FRAME_MS, self._tick)

    def _destroy(self):
        for win, *_ in self._strips:
            try:
                win.destroy()
            except Exception:
                pass
        self._strips.clear()
        self._mgr._done(self)


def _make_click_through(win) -> None:
    """Best-effort: let mouse clicks pass through the strip (Windows only)."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_TOOLWINDOW = 0x00000080
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW,
        )
    except Exception:
        pass  # purely cosmetic; the border still shows if this fails


class _Manager:
    """Owns the single Tk root + overlay thread; dispatches flash requests."""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._root = None
        self._gradient = None
        self._active: set[_Flash] = set()

    def flash(self, rect, duration: float = DEFAULT_DURATION) -> None:
        """Flash a gradient border around ``rect`` = (x, y, w, h), or the whole
        screen if ``rect`` is None. Safe to call from any thread."""
        if not self._ensure_thread():
            return
        self._q.put((rect, duration))

    def _ensure_thread(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            try:
                import tkinter  # noqa: F401
                from PIL import ImageTk  # noqa: F401
            except Exception as exc:
                log.debug("send overlay unavailable (no tkinter/ImageTk): %s", exc)
                return False
            self._thread = threading.Thread(
                target=self._run, name="airthrow-overlay", daemon=True)
            self._thread.start()
            return True

    def _run(self) -> None:
        try:
            import tkinter as tk
            self._gradient = _build_gradient()
            self._root = tk.Tk()
            self._root.withdraw()  # the root itself is never shown
            self._poll()
            self._root.mainloop()
        except Exception as exc:
            log.debug("send overlay thread stopped: %s", exc)

    def _poll(self) -> None:
        try:
            while True:
                rect, duration = self._q.get_nowait()
                try:
                    self._active.add(_Flash(self, self._root, self._gradient,
                                            rect, duration))
                except Exception as exc:
                    log.debug("could not show send border: %s", exc)
        except queue.Empty:
            pass
        self._root.after(40, self._poll)

    def _done(self, flash: _Flash) -> None:
        self._active.discard(flash)


_manager = _Manager()


def flash(rect, duration: float = DEFAULT_DURATION) -> None:
    """Public entrypoint — see :meth:`_Manager.flash`."""
    _manager.flash(rect, duration)
