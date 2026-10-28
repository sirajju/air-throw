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


def _foreground_linux() -> tuple[int | None, str]:
    import subprocess

    def run(cmd: list[str]) -> str:
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=2
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    win_id = run(["xdotool", "getactivewindow"])
    if not win_id:
        return (None, "")
    title = run(["xdotool", "getwindowname", win_id])
    pid_str = run(["xdotool", "getwindowpid", win_id])
    pid = int(pid_str) if pid_str.isdigit() else None
    return (pid, title)


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
                if p.suffix.lower() in IMAGE_EXTENSIONS and p.exists():
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
    """Best guess at the image open in the foreground window, or None."""
    pid, title = foreground_window()
    if pid:
        hit = _open_image_of_pid(pid)
        if hit:
            return hit
    return _image_from_title(title)
