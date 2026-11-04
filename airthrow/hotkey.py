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
    """Registers and manages a global hotkey via pynput.

    Matching is done by PHYSICAL key (virtual-key code) + live modifier state,
    not by the typed character. pynput's GlobalHotKeys compares characters, so
    on layouts where Ctrl+Alt acts as AltGr (composing e.g. 'ā' from A) the
    string '<ctrl>+<alt>+a' never matches — the shortcut silently did nothing.
    AltGr itself counts as Ctrl+Alt held, matching what Windows reports.
    """

    DEBOUNCE = 1.0  # ignore repeats within this many seconds (key auto-repeat)

    def __init__(self, callback: Callable[[], None], hotkey_str: str = "<ctrl>+<alt>+a"):
        self.callback = callback
        self.hotkey_str = normalize_hotkey(hotkey_str)
        self._listener = None
        self._running = False
        self._mods: set[str] = set()
        self._last_fire = 0.0
        self._need_mods, self._key_vk, self._key_char, self._key_named = \
            self._parse(self.hotkey_str)

    @staticmethod
    def _parse(hotkey: str):
        """-> (required modifiers, vk of the main key, its char, named Key)."""
        mods: set[str] = set()
        key_vk = None
        key_char = None
        key_named = None
        for part in hotkey.split("+"):
            part = part.strip().lower()
            if part in ("<ctrl>", "<alt>", "<shift>", "<cmd>"):
                mods.add(part.strip("<>"))
            elif part:
                token = part.strip("<>")
                if len(token) == 1:
                    key_char = token
                    key_vk = ord(token.upper())  # VK_A..VK_Z / VK_0..9 on Windows
                else:
                    key_named = token  # e.g. f5, space, insert
        return mods, key_vk, key_char, key_named

    def _mod_name(self, key, keyboard):
        if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            return {"ctrl"}
        if key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r):
            return {"alt"}
        if key == keyboard.Key.alt_gr:
            return {"alt", "ctrl"}  # Windows reports AltGr as Ctrl+Alt
        if key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
            return {"shift"}
        if key in (keyboard.Key.cmd, getattr(keyboard.Key, "cmd_l", None),
                   getattr(keyboard.Key, "cmd_r", None)):
            return {"cmd"}
        return None

    def _matches(self, key, keyboard) -> bool:
        if self._key_named:
            named = getattr(keyboard.Key, self._key_named, None)
            if named is not None and key == named:
                return True
        vk = getattr(key, "vk", None)
        if vk is None and hasattr(key, "value"):
            vk = getattr(key.value, "vk", None)
        if self._key_vk is not None and vk == self._key_vk:
            return True
        ch = getattr(key, "char", None)
        return bool(ch and self._key_char and ch.lower() == self._key_char)

    def start(self) -> bool:
        if self._running:
            return True
        try:
            import time

            from pynput import keyboard

            def on_press(key):
                mods = self._mod_name(key, keyboard)
                if mods:
                    self._mods |= mods
                    return
                if not self._matches(key, keyboard):
                    return
                if not self._need_mods <= self._mods:
                    return
                now = time.time()
                if now - self._last_fire < self.DEBOUNCE:
                    return
                self._last_fire = now
                log.info("Global shortcut fired (%s)",
                         format_hotkey_display(self.hotkey_str))
                try:
                    self.callback()
                except Exception as exc:
                    log.error("hotkey callback failed: %s", exc)

            def on_release(key):
                mods = self._mod_name(key, keyboard)
                if mods:
                    self._mods -= mods

            self._listener = keyboard.Listener(on_press=on_press,
                                               on_release=on_release)
            self._listener.daemon = True
            self._listener.start()
            self._running = True
            log.info("Global shortcut active: %s (%s)",
                     format_hotkey_display(self.hotkey_str), self.hotkey_str)
            return True
        except Exception as exc:
            log.warning("Could not register global hotkey '%s': %s",
                        self.hotkey_str, exc)
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
