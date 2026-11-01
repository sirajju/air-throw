"""Global hotkey listener for AirThrow.

Listens for a configurable system-wide keyboard shortcut (default Ctrl+Alt+A)
and triggers a gesture send session. Works on Windows and Linux (X11/Xwayland)
via pynput.
"""

from __future__ import annotations

from typing import Callable

from .logs import get_logger

log = get_logger()


def normalize_hotkey(s: str) -> str:
    """Convert human-friendly hotkey strings into pynput's <ctrl>+<alt>+a format."""
    parts = [p.strip().lower() for p in s.replace("-", "+").split("+")]
    mods = {
        "ctrl": "<ctrl>",
        "control": "<ctrl>",
        "alt": "<alt>",
        "shift": "<shift>",
        "super": "<cmd>",
        "win": "<cmd>",
        "cmd": "<cmd>",
        "meta": "<cmd>",
    }
    norm = []
    for p in parts:
        if not p:
            continue
        if p in mods:
            norm.append(mods[p])
        elif p.startswith("<") and p.endswith(">"):
            norm.append(p)
        else:
            norm.append(p)
    return "+".join(norm)


def format_hotkey_display(s: str) -> str:
    """Format pynput hotkey strings into pretty user-facing text (e.g. Ctrl+Alt+A)."""
    clean = s.replace("<ctrl>", "Ctrl").replace("<alt>", "Alt").replace("<shift>", "Shift").replace("<cmd>", "Super")
    parts = [p.strip() for p in clean.split("+") if p.strip()]
    return "+".join(p.upper() if len(p) == 1 else p.capitalize() for p in parts)


class HotkeyListener:
    """Registers and manages a global hotkey via pynput."""

    def __init__(self, callback: Callable[[], None], hotkey_str: str = "<ctrl>+<alt>+a"):
        self.callback = callback
        self.hotkey_str = normalize_hotkey(hotkey_str)
        self._listener = None
        self._running = False

    def start(self) -> bool:
        if self._running:
            return True
        try:
            from pynput import keyboard

            self._listener = keyboard.GlobalHotKeys({self.hotkey_str: self.callback})
            self._listener.daemon = True
            self._listener.start()
            self._running = True
            log.info("Global shortcut active: %s (%s)",
                     format_hotkey_display(self.hotkey_str), self.hotkey_str)
            return True
        except Exception as exc:
            log.warning("Could not register global hotkey '%s': %s", self.hotkey_str, exc)
            self._listener = None
            self._running = False
            return False

    def stop(self) -> None:
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._running = False
