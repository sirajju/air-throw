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

# Named border styles. Each is a list of RGB stops the gradient flows through;
# the last stop equals the first so the scroll wraps without a visible seam.
STYLES: dict[str, list[tuple[int, int, int]]] = {
    "aurora": [(0, 229, 200), (33, 150, 243), (124, 77, 255), (255, 77, 157),
               (0, 229, 200)],                       # teal -> blue -> violet -> pink
    "rainbow": [(255, 40, 40), (255, 160, 0), (240, 230, 0), (40, 210, 90),
                (0, 150, 255), (150, 50, 255), (255, 40, 40)],
    "ocean": [(0, 180, 255), (0, 230, 200), (40, 120, 255), (0, 210, 230),
              (0, 180, 255)],                         # cool cyans & blues
    "sunset": [(255, 60, 0), (255, 150, 0), (255, 220, 70), (255, 70, 120),
               (255, 60, 0)],                         # warm fire tones
    "mono": [(0, 229, 200), (210, 255, 250), (0, 229, 200)],  # teal glow pulse
    # Internal, not user-selectable: the green "a file landed" receive flash.
    "incoming": [(57, 211, 83), (0, 230, 160), (120, 255, 180), (57, 211, 83)],
}
STYLE_NAMES = ("aurora", "rainbow", "ocean", "sunset", "mono")  # user-pickable
DEFAULT_STYLE = "aurora"
RECEIVE_STYLE = "incoming"


def _build_gradient(stops):
    """A (PERIOD, 3) uint8 numpy array cycling smoothly through ``stops``."""
    import numpy as np

    seg = PERIOD / (len(stops) - 1)
    out = np.zeros((PERIOD, 3), dtype=np.uint8)
    for i in range(PERIOD):
        pos = i / seg
        a = int(pos)
        f = pos - a
        c0 = stops[a]
        c1 = stops[min(a + 1, len(stops) - 1)]
        out[i] = [int(c0[k] + (c1[k] - c0[k]) * f) for k in range(3)]
    return out


def _gradient_slice(Image, ImageTk, grad, orient, length, offset,
                    thickness=THICKNESS):
    """A PhotoImage of a `thickness`-wide slice of the scrolling gradient."""
    import numpy as np

    idx = (np.arange(length) + offset) % PERIOD
    line = grad[idx]  # (length, 3)
    if orient == "h":
        arr = np.broadcast_to(line[None, :, :], (thickness, length, 3))
    else:
        arr = np.broadcast_to(line[:, None, :], (length, thickness, 3))
    return ImageTk.PhotoImage(Image.fromarray(np.ascontiguousarray(arr), "RGB"))


def _border_specs(x, y, w, h, t):
    """(geometry, orientation, length) for the 4 strips framing a rectangle."""
    return [
        (f"{w}x{t}+{x}+{y}", "h", w),
        (f"{w}x{t}+{x}+{y + h - t}", "h", w),
        (f"{t}x{h}+{x}+{y}", "v", h),
        (f"{t}x{h}+{x + w - t}+{y}", "v", h),
    ]


class _Flash:
    """One border animation around a rectangle, living on the overlay thread."""

    def __init__(self, manager, root, gradient, rect, duration):
        self._mgr = manager
        self._root = root
        self._grad = gradient  # (PERIOD, 3) uint8 for the chosen style
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

        # (geometry string, orientation) for top, bottom, left, right.
        specs = _border_specs(x, y, w, h, THICKNESS)
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
        return _gradient_slice(self._Image, self._ImageTk, self._grad,
                               orient, length, offset)

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


def _load_thumb(Image, path, maxdim=220):
    """Load an image as a small PIL RGB thumbnail; fall back to a gradient tile
    (the generic 'object') if there's no path or it can't be read."""
    try:
        if path:
            img = Image.open(path).convert("RGB")
            img.thumbnail((maxdim, maxdim))
            return img
    except Exception:
        pass
    # Generic object: a small aurora-gradient square.
    import numpy as np
    grad = _build_gradient(STYLES[DEFAULT_STYLE])
    side = 140
    idx = (np.arange(side) * (PERIOD / side)).astype(int) % PERIOD
    row = grad[idx]
    arr = np.broadcast_to(row[None, :, :], (side, side, 3))
    return Image.fromarray(np.ascontiguousarray(arr), "RGB")


class _Fly:
    """A thumbnail that flies across the screen: off toward the throw direction
    on the sender, or in from the sender's side on the receiver."""

    DURATION = 0.72

    def __init__(self, manager, root, mode, direction, path):
        self._mgr = manager
        self._root = root
        self._start_t = time.time()
        self._hold_done = False

        import tkinter as tk
        from PIL import Image, ImageTk
        self._tk, self._Image, self._ImageTk = tk, Image, ImageTk

        self._base = _load_thumb(Image, path)
        self._bw, self._bh = self._base.size
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        cy = sh // 2
        off_r, off_l = sw + 240, -240

        if mode == "out":  # sender: center -> toward the throw direction
            self._p0 = (sw // 2, cy)
            self._p1 = (off_r if direction == "right" else off_l, cy)
            self._s0, self._s1, self._a0, self._a1 = 1.0, 0.45, 1.0, 0.05
            self._hold = 0.0
        else:              # receiver: in from the sender's side -> center
            self._p0 = (off_l if direction == "left" else off_r, cy)
            self._p1 = (sw // 2, cy)
            self._s0, self._s1, self._a0, self._a1 = 0.45, 1.0, 0.15, 1.0
            self._hold = 0.55

        self._win = tk.Toplevel(root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._lbl = tk.Label(self._win, bd=0, highlightthickness=0, bg="#000000")
        self._lbl.pack()
        _make_click_through(self._win)
        self._tick()

    def _tick(self):
        now = time.time()
        t = (now - self._start_t) / self.DURATION
        if t >= 1.0:
            if self._hold and not self._hold_done:
                self._hold_done = True
                self._render(1.0)
                self._root.after(int(self._hold * 1000), self._destroy)
                return
            self._destroy()
            return
        self._render(t)
        self._root.after(FRAME_MS, self._tick)

    def _render(self, t):
        e = 1 - (1 - t) ** 2  # ease-out
        cx = self._p0[0] + (self._p1[0] - self._p0[0]) * e
        cy = self._p0[1] + (self._p1[1] - self._p0[1]) * e
        scale = self._s0 + (self._s1 - self._s0) * e
        alpha = self._a0 + (self._a1 - self._a0) * e
        w = max(1, int(self._bw * scale))
        h = max(1, int(self._bh * scale))
        img = self._base.resize((w, h))
        photo = self._ImageTk.PhotoImage(img)
        self._lbl.configure(image=photo)
        self._lbl.image = photo
        self._win.geometry(f"{w}x{h}+{int(cx - w / 2)}+{int(cy - h / 2)}")
        try:
            self._win.attributes("-alpha", max(0.0, min(1.0, alpha)))
        except self._tk.TclError:
            pass

    def _destroy(self):
        try:
            self._win.destroy()
        except Exception:
            pass
        self._mgr._done_fly(self)


class _Selector:
    """A centered, gradient-bordered picker listing device names.

    Its border animates continuously while open, and it stays up until it is
    explicitly hidden — it is NOT tied to per-frame hand presence, so a flickery
    gesture can't make it blink. The app decides which row is highlighted and
    when to confirm; this class only draws.
    """

    PAD = 16
    ROW_H = 38
    TITLE_H = 40

    def __init__(self, manager, root, gradient, names, title):
        self._mgr = manager
        self._root = root
        self._grad = gradient
        self._names = list(names)
        self._index = 0
        self._alive = True
        self._strips = []
        self._row_labels = []

        import tkinter as tk
        from PIL import Image, ImageTk
        self._tk, self._Image, self._ImageTk = tk, Image, ImageTk

        longest = max([len(title)] + [len(n) for n in self._names] + [8])
        pw = min(max(300, longest * 12 + 90), root.winfo_screenwidth() - 80)
        ph = self.TITLE_H + len(self._names) * self.ROW_H + self.PAD
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        px = (sw - pw) // 2
        py = (sh - ph) // 2

        # Animated gradient border framing the panel.
        bx, by = px - THICKNESS, py - THICKNESS
        bw, bh = pw + 2 * THICKNESS, ph + 2 * THICKNESS
        for geom, orient, length in _border_specs(bx, by, bw, bh, THICKNESS):
            win = tk.Toplevel(root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.geometry(geom)
            lbl = tk.Label(win, bd=0, highlightthickness=0, bg="#000000")
            lbl.pack(fill="both", expand=True)
            _make_click_through(win)
            self._strips.append([win, lbl, orient, max(1, length)])

        # The dark panel with the title + one row per device.
        panel = tk.Toplevel(root)
        panel.overrideredirect(True)
        panel.attributes("-topmost", True)
        panel.geometry(f"{pw}x{ph}+{px}+{py}")
        panel.configure(bg="#1b1d23")
        self._panel = panel
        tk.Label(panel, text=title, bg="#1b1d23", fg="#00e5c8",
                 font=("Segoe UI Semibold", 13)).pack(pady=(10, 4))
        rows = tk.Frame(panel, bg="#1b1d23")
        rows.pack(fill="both", expand=True, padx=10)
        for name in self._names:
            row = tk.Label(rows, text=name, anchor="w", bg="#23262e",
                           fg="#e6e8ee", font=("Segoe UI", 12), padx=14, pady=6)
            row.pack(fill="x", pady=2)
            self._row_labels.append(row)
        _make_click_through(panel)

        self.set_index(0)
        self._animate()

    def set_index(self, index: int) -> None:
        if not self._names:
            return
        self._index = index % len(self._names)
        for i, row in enumerate(self._row_labels):
            if i == self._index:
                row.configure(bg="#00e5c8", fg="#06231f",
                              text=f"▶  {self._names[i]}")
            else:
                row.configure(bg="#23262e", fg="#e6e8ee", text=self._names[i])

    def current(self) -> int:
        return self._index

    def _animate(self) -> None:
        if not self._alive:
            return
        offset = int(time.time() * SCROLL_PX_PER_S) % PERIOD
        for win, lbl, orient, length in self._strips:
            photo = _gradient_slice(self._Image, self._ImageTk, self._grad,
                                    orient, length, offset)
            lbl.configure(image=photo)
            lbl.image = photo
        self._root.after(FRAME_MS, self._animate)

    def destroy(self) -> None:
        self._alive = False
        for win, *_ in self._strips:
            try:
                win.destroy()
            except Exception:
                pass
        self._strips.clear()
        try:
            self._panel.destroy()
        except Exception:
            pass


class _Radar:
    """The canvas 'map': YOU at centre, each device at the position you placed it.
    The app highlights whichever device your hand points toward. Stays up until
    hidden (debounced by the app), so it doesn't blink with a flickery hand."""

    def __init__(self, manager, root, targets, title):
        self._mgr = manager
        self._root = root
        self._targets = targets  # list of (name, x, y) in 0..1
        self._index = -1

        import tkinter as tk
        self._tk = tk
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self._w = int(sw * 0.62)
        self._h = int(sh * 0.62)
        px, py = (sw - self._w) // 2, (sh - self._h) // 2

        self._win = tk.Toplevel(root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        try:
            self._win.attributes("-alpha", 0.95)
        except tk.TclError:
            pass
        self._win.geometry(f"{self._w}x{self._h}+{px}+{py}")
        self._cv = tk.Canvas(self._win, width=self._w, height=self._h,
                             bg="#15171c", highlightthickness=2,
                             highlightbackground="#00e5c8")
        self._cv.pack(fill="both", expand=True)
        _make_click_through(self._win)
        self._title = title
        self._redraw()

    def _pt(self, x, y):
        pad = 70
        cx = pad + x * (self._w - 2 * pad)
        cy = pad + y * (self._h - 2 * pad)
        return cx, cy

    def set_index(self, index):
        self._index = index
        self._redraw()

    def _redraw(self):
        c = self._cv
        c.delete("all")
        cx, cy = self._w / 2, self._h / 2
        c.create_text(self._w / 2, 28, text=self._title, fill="#00e5c8",
                      font=("Segoe UI Semibold", 16))
        # concentric range rings
        for r in (0.18, 0.34, 0.5):
            rr = r * min(self._w, self._h)
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline="#2a2e37")
        # lines + device dots
        for i, (name, x, y) in enumerate(self._targets):
            tx, ty = self._pt(x, y)
            active = i == self._index
            col = "#00e5c8" if active else "#7f8795"
            width = 4 if active else 2
            c.create_line(cx, cy, tx, ty, fill=col, width=width)
            r = 16 if active else 10
            c.create_oval(tx - r, ty - r, tx + r, ty + r,
                          fill=col, outline="")
            c.create_text(tx, ty - r - 12, text=name,
                          fill="#ffffff" if active else "#b9bfca",
                          font=("Segoe UI Semibold" if active else "Segoe UI",
                                12 if active else 11))
        # YOU at centre
        c.create_oval(cx - 12, cy - 12, cx + 12, cy + 12, fill="#ff5d9e",
                      outline="#ffffff", width=2)
        c.create_text(cx, cy + 26, text="YOU", fill="#ffb3d1",
                      font=("Segoe UI Semibold", 11))
        hint = ("Reach your hand toward a device, then hold"
                if self._index < 0 else
                f"Hold to send to {self._targets[self._index][0]}")
        c.create_text(self._w / 2, self._h - 24, text=hint, fill="#9aa0ad",
                      font=("Segoe UI", 12))

    def destroy(self):
        try:
            self._win.destroy()
        except Exception:
            pass


class _Manager:
    """Owns the single Tk root + overlay thread; dispatches flash requests."""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._root = None
        self._grad_cache: dict[str, object] = {}  # style name -> gradient array
        self._active: set[_Flash] = set()
        self._flies: set = set()
        self._selector: _Selector | None = None
        self._radar: _Radar | None = None

    def flash(self, rect, duration: float = DEFAULT_DURATION,
              style: str | None = None) -> None:
        """Flash a gradient border around ``rect`` = (x, y, w, h), or the whole
        screen if ``rect`` is None, in the named ``style`` (see STYLES). Safe to
        call from any thread."""
        if not self._ensure_thread():
            return
        self._q.put(("flash", rect, duration, style or DEFAULT_STYLE))

    def fly(self, mode: str, direction: str, path) -> None:
        """Fly a thumbnail: mode 'out' (sender) or 'in' (receiver)."""
        if not self._ensure_thread():
            return
        self._q.put(("fly", mode, direction, str(path) if path else ""))

    def selector_show(self, names, title: str = "Throw to…",
                      style: str | None = None) -> None:
        if not self._ensure_thread():
            return
        self._q.put(("sel_show", list(names), title, style or DEFAULT_STYLE))

    def selector_set(self, index: int) -> None:
        if self._thread and self._thread.is_alive():
            self._q.put(("sel_set", index))

    def selector_hide(self) -> None:
        if self._thread and self._thread.is_alive():
            self._q.put(("sel_hide",))

    def radar_show(self, targets, title: str = "Throw to…") -> None:
        if not self._ensure_thread():
            return
        self._q.put(("radar_show", list(targets), title))

    def radar_set(self, index: int) -> None:
        if self._thread and self._thread.is_alive():
            self._q.put(("radar_set", index))

    def radar_hide(self) -> None:
        if self._thread and self._thread.is_alive():
            self._q.put(("radar_hide",))

    def _gradient_for(self, style: str):
        if style not in self._grad_cache:
            stops = STYLES.get(style, STYLES[DEFAULT_STYLE])
            self._grad_cache[style] = _build_gradient(stops)
        return self._grad_cache[style]

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
            self._root = tk.Tk()
            self._root.withdraw()  # the root itself is never shown
            self._poll()
            self._root.mainloop()
        except Exception as exc:
            log.debug("send overlay thread stopped: %s", exc)

    def _poll(self) -> None:
        try:
            while True:
                cmd = self._q.get_nowait()
                try:
                    self._dispatch(cmd)
                except Exception as exc:
                    log.debug("overlay command %s failed: %s", cmd[0], exc)
        except queue.Empty:
            pass
        self._root.after(40, self._poll)

    def _dispatch(self, cmd) -> None:
        kind = cmd[0]
        if kind == "flash":
            _, rect, duration, style = cmd
            gradient = self._gradient_for(style)
            self._active.add(_Flash(self, self._root, gradient, rect, duration))
        elif kind == "fly":
            _, mode, direction, path = cmd
            self._flies.add(_Fly(self, self._root, mode, direction, path or None))
        elif kind == "sel_show":
            _, names, title, style = cmd
            if self._selector:
                self._selector.destroy()
            self._selector = _Selector(self, self._root,
                                       self._gradient_for(style), names, title)
        elif kind == "sel_set":
            if self._selector:
                self._selector.set_index(cmd[1])
        elif kind == "sel_hide":
            if self._selector:
                self._selector.destroy()
                self._selector = None
        elif kind == "radar_show":
            _, targets, title = cmd
            if self._radar:
                self._radar.destroy()
            self._radar = _Radar(self, self._root, targets, title)
        elif kind == "radar_set":
            if self._radar:
                self._radar.set_index(cmd[1])
        elif kind == "radar_hide":
            if self._radar:
                self._radar.destroy()
                self._radar = None

    def _done(self, flash: _Flash) -> None:
        self._active.discard(flash)

    def _done_fly(self, fly) -> None:
        self._flies.discard(fly)


_manager = _Manager()


def flash(rect, duration: float = DEFAULT_DURATION,
          style: str | None = None) -> None:
    """Public entrypoint — see :meth:`_Manager.flash`."""
    _manager.flash(rect, duration, style)


def fly_out(path, direction: str) -> None:
    """Sender: fly the image off toward `direction` ('left'/'right')."""
    _manager.fly("out", direction, path)


def fly_in(path, from_direction: str) -> None:
    """Receiver: fly the image in from `from_direction` ('left'/'right')."""
    _manager.fly("in", from_direction, path)


def selector_show(names, title: str = "Throw to…",
                  style: str | None = None) -> None:
    """Open the device picker listing ``names`` (stays up until hidden)."""
    _manager.selector_show(names, title, style)


def selector_set(index: int) -> None:
    """Highlight row ``index`` in the open picker."""
    _manager.selector_set(index)


def selector_hide() -> None:
    """Close the picker."""
    _manager.selector_hide()


def radar_show(targets, title: str = "Throw to…") -> None:
    """Show the canvas map; `targets` = list of (name, x, y) in 0..1."""
    _manager.radar_show(targets, title)


def radar_set(index: int) -> None:
    """Highlight the target at `index` on the map (-1 = none)."""
    _manager.radar_set(index)


def radar_hide() -> None:
    """Close the canvas map."""
    _manager.radar_hide()
