"""Start AirThrow (tray mode) automatically on login.

Windows: a shortcut in the user's Startup folder.
Linux:   a ~/.config/autostart/airthrow.desktop entry.

The launch target prefers the windowed `airthrow-tray` executable when frozen,
so no console window appears; otherwise it falls back to `<python> -m airthrow
tray` (or the console exe's `tray` command).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "AirThrow"


def _launch_target() -> tuple[str, list[str]]:
    """Return (program, args) that starts the background tray app."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        tray_name = "airthrow-tray.exe" if sys.platform.startswith("win") else "airthrow-tray"
        tray = exe_dir / tray_name
        if tray.exists():
            return str(tray), []
        return sys.executable, ["tray"]  # console exe fallback
    return sys.executable, ["-m", "airthrow", "tray"]


def _command_string() -> str:
    prog, args = _launch_target()
    quoted = f'"{prog}"' if " " in prog else prog
    return " ".join([quoted, *args]).strip()


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------
def _win_shortcut_path() -> Path:
    startup = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / \
        "Start Menu" / "Programs" / "Startup"
    return startup / f"{APP_NAME}.lnk"


def _win_enable() -> str:
    import win32com.client  # from pywin32

    lnk = _win_shortcut_path()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    prog, args = _launch_target()
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(str(lnk))
    shortcut.TargetPath = prog
    shortcut.Arguments = " ".join(args)
    shortcut.WorkingDirectory = str(Path(prog).parent)
    shortcut.Description = "AirThrow — throw images across the air"
    shortcut.Save()
    return str(lnk)


def _win_disable() -> bool:
    lnk = _win_shortcut_path()
    if lnk.exists():
        lnk.unlink()
        return True
    return False


def _win_status() -> bool:
    return _win_shortcut_path().exists()


# --------------------------------------------------------------------------
# Linux
# --------------------------------------------------------------------------
def _linux_desktop_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / "airthrow.desktop"


def _linux_enable() -> str:
    path = _linux_desktop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={_command_string()}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )
    return str(path)


def _linux_disable() -> bool:
    path = _linux_desktop_path()
    if path.exists():
        path.unlink()
        return True
    return False


def _linux_status() -> bool:
    return _linux_desktop_path().exists()


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def enable() -> str:
    if sys.platform.startswith("win"):
        return _win_enable()
    if sys.platform.startswith("linux"):
        return _linux_enable()
    raise RuntimeError(f"autostart not supported on {sys.platform}")


def disable() -> bool:
    if sys.platform.startswith("win"):
        return _win_disable()
    if sys.platform.startswith("linux"):
        return _linux_disable()
    raise RuntimeError(f"autostart not supported on {sys.platform}")


def status() -> bool:
    if sys.platform.startswith("win"):
        return _win_status()
    if sys.platform.startswith("linux"):
        return _linux_status()
    return False
