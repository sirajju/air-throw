"""Find the image file open in the foreground window.

Unified approach that works on Windows and Linux:
  1. Resolve the foreground/active window's process id (PID).
  2. Ask the OS which files that process currently has open (via psutil) and
     pick the most relevant image file.
  3. Fall back to parsing the window title for a path or known filename.

This is heuristic by nature: some viewers open a file, read it, and release the
handle immediately (so step 2 misses it), while others keep it locked. The title
fallback covers the common "Photos — sunset.jpg" style window captions.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psutil

from . import IMAGE_EXTENSIONS

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

# Images under these paths are app chrome/decoration (e.g. WinUI's
# NoiseAsset_256x256_PNG), never a user's photo — ignore them. Markers are
# written with forward slashes; the path is normalised before matching so this
# works on both Windows (\) and Linux (/).
_SYSTEM_MARKERS = (
    "/windows/", "/program files", "windowsapps", "/appdata/local/packages/",
    "/usr/share/", "/usr/lib/", "/snap/", "site-packages", "dist-packages",
)


def _is_system_asset(path: Path) -> bool:
    p = str(path).lower().replace("\\", "/")
    return any(marker in p for marker in _SYSTEM_MARKERS)


# --------------------------------------------------------------------------
# Foreground window -> (pid, title)
# --------------------------------------------------------------------------
def foreground_window() -> tuple[int | None, str]:
    """Return (pid, title) of the active window, or (None, "") if unknown."""
    if IS_WINDOWS:
        return _foreground_windows()
    if IS_LINUX:
        return _foreground_linux()
    return (None, "")


def _foreground_windows() -> tuple[int | None, str]:
    try:
        import win32gui
        import win32process
    except ImportError:
        return (None, "")
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return (None, "")
    title = win32gui.GetWindowText(hwnd) or ""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        pid = None
    return (pid, title)


def _visible_windows_windows() -> list[tuple[int, str]]:
    """(pid, title) for every visible top-level window, top-most Z-order first.

    Lets us find the image viewer even when it isn't the focused window —
    e.g. while the AirThrow preview has focus during a gesture."""
    try:
        import win32gui
        import win32process
    except ImportError:
        return []
    out: list[tuple[int, str]] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        out.append((pid, title))

    try:
        win32gui.EnumWindows(cb, None)  # enumerates top (foreground) to bottom
    except Exception:
        return []
    return out


def _foreground_linux() -> tuple[int | None, str]:
    win_id = _xdotool("getactivewindow")
    if not win_id:
        return (None, "")
    title = _xdotool("getwindowname", win_id)
    pid_str = _xdotool("getwindowpid", win_id)
    pid = int(pid_str) if pid_str.isdigit() else None
    return (pid, title)


def _xdotool(*args: str) -> str:
    import subprocess

    try:
        return subprocess.run(
            ["xdotool", *args], capture_output=True, text=True, timeout=2
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# --------------------------------------------------------------------------
# Foreground window -> on-screen rectangle (x, y, w, h), for the send animation
# --------------------------------------------------------------------------
def foreground_rect() -> tuple[int, int, int, int] | None:
    """Screen rectangle of the active window, or None if it can't be read."""
    if IS_WINDOWS:
        return _foreground_rect_windows()
    if IS_LINUX:
        return _foreground_rect_linux()
    return None


def _foreground_rect_windows() -> tuple[int, int, int, int] | None:
    try:
        import win32gui
    except ImportError:
        return None
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    except Exception:
        return None
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        return None
    return (left, top, w, h)


def _foreground_rect_linux() -> tuple[int, int, int, int] | None:
    win_id = _xdotool("getactivewindow")
    if not win_id:
        return None
    out = _xdotool("getwindowgeometry", "--shell", win_id)
    vals: dict[str, int] = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            if val.strip().lstrip("-").isdigit():
                vals[key.strip()] = int(val.strip())
    try:
        return (vals["X"], vals["Y"], vals["WIDTH"], vals["HEIGHT"])
    except KeyError:
        return None


# --------------------------------------------------------------------------
# PID -> open image file
# --------------------------------------------------------------------------
def _open_image_of_pid(pid: int) -> Path | None:
    """Most recently modified image file the process (or a child) has open."""
    candidates: list[Path] = []
    try:
        procs = [psutil.Process(pid)]
        procs.extend(procs[0].children(recursive=True))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None

    for proc in procs:
        try:
            for of in proc.open_files():
                p = Path(of.path)
                if (p.suffix.lower() in IMAGE_EXTENSIONS
                        and not _is_system_asset(p)
                        and p.exists()):
                    candidates.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not candidates:
        return None
    # Prefer the most recently modified — usually the one just opened.
    return max(candidates, key=lambda p: p.stat().st_mtime)


# --------------------------------------------------------------------------
# Title -> path (fallback)
# --------------------------------------------------------------------------
def _image_from_title(title: str) -> Path | None:
    if not title:
        return None
    # Case 1: the title literally contains a full path.
    for token in title.replace("\\", "/").split():
        candidate = Path(token)
        if candidate.suffix.lower() in IMAGE_EXTENSIONS and candidate.exists():
            return candidate

    # Case 2: the title ends in a bare filename like "sunset.jpg"; search the
    # usual image folders for it.
    words = title.replace("—", " ").replace("-", " ").replace("|", " ").split()
    for word in reversed(words):
        if Path(word).suffix.lower() in IMAGE_EXTENSIONS:
            found = _search_common_dirs(word)
            if found:
                return found
    return None


def _search_common_dirs(filename: str) -> Path | None:
    home = Path.home()
    search_roots = [
        home / "Pictures",
        home / "Downloads",
        home / "Desktop",
        Path.cwd(),
    ]
    for root in search_roots:
        hit = root / filename
        if hit.exists():
            return hit
    return None


# --------------------------------------------------------------------------
# Public entrypoint
# --------------------------------------------------------------------------
def current_image() -> Path | None:
    """Best guess at the image the user has open, or None.

    Tries the focused window first; if that's our own preview (or has no image),
    scans the other visible windows top-first so the image viewer is still found
    while the AirThrow preview holds focus during a gesture."""
    pid, title = foreground_window()
    if pid and "airthrow" not in (title or "").lower():
        if _pid_scannable(pid):  # heavy apps (browser/IDE/terminal) skipped
            hit = _open_image_of_pid(pid)
            if hit:
                return hit
        hit = _image_from_title(title)
        if hit:
            return hit

    # Focused window gave nothing (or is our preview): scan other windows.
    if IS_WINDOWS:
        hit = _scan_other_windows()
        if hit:
            return hit

    return _image_from_title(title)


# Processes whose open_files() are huge and never a user's photo: browsers and
# electron apps hold thousands of handles and psutil takes seconds on them
# (while holding the GIL), which starved the camera loop and lagged the preview.
_SKIP_PROCS = {
    "chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe",
    "vivaldi.exe", "code.exe", "cursor.exe", "devenv.exe", "idea64.exe",
    "pycharm64.exe", "windowsterminal.exe", "wt.exe", "powershell.exe",
    "pwsh.exe", "cmd.exe", "python.exe", "pythonw.exe", "airthrow.exe",
    "airthrow-gui.exe", "airthrow-tray.exe", "discord.exe", "slack.exe",
    "teams.exe", "ms-teams.exe", "spotify.exe", "steam.exe", "onedrive.exe",
    "explorer.exe", "searchhost.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "conhost.exe", "openconsole.exe",
}


def _pid_scannable(pid: int) -> bool:
    """False for processes whose open_files() take seconds (and never hold a
    user's photo) — browsers, IDEs, terminals, the shell."""
    try:
        return psutil.Process(pid).name().lower() not in _SKIP_PROCS
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False

_SCAN_INTERVAL = 5.0   # full window-scan at most this often
_SCAN_BUDGET = 1.2     # and never longer than this per scan
_MAX_WINDOWS = 10      # only the top of the Z-order matters
_scan_cache: dict = {"t": 0.0, "result": None}


def _scan_other_windows() -> Path | None:
    """Bounded, cached scan of non-focused windows for an open image."""
    import time as _time

    now = _time.time()
    if now - _scan_cache["t"] < _SCAN_INTERVAL:
        return _scan_cache["result"]
    _scan_cache["t"] = now

    start = _time.perf_counter()
    result: Path | None = None
    windows = [(p, t) for p, t in _visible_windows_windows()
               if "airthrow" not in t.lower()][:_MAX_WINDOWS]

    seen: set[int] = set()
    for wpid, _wtitle in windows:
        if wpid in seen:
            continue
        seen.add(wpid)
        if not _pid_scannable(wpid):
            continue
        hit = _open_image_of_pid(wpid)
        if hit:
            result = hit
            break
        if _time.perf_counter() - start > _SCAN_BUDGET:
            break  # stay cheap; try again on the next scan window

    if result is None:
        for _wpid, wtitle in windows:
            hit = _image_from_title(wtitle)
            if hit:
                result = hit
                break

    _scan_cache["result"] = result
    return result
