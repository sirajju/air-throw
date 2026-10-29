"""Run AirThrow in the background and start it automatically on login.

Two levels:

* **autostart** — a login entry (Windows Startup shortcut / Linux
  ``~/.config/autostart`` desktop file) that launches the tray app.
* **service** — a managed background agent: a **systemd user service** on Linux
  (auto-restart, ``systemctl --user``) and a hidden login agent on Windows.

A real OS service (Windows Service / systemd *system* unit) runs with no access
to the webcam, display or on-screen overlays, so AirThrow runs as a per-user
background agent instead.

Everything works both for the frozen executables and for a from-source (venv)
install — launches use ``pythonw`` on Windows (no console), set the repo as the
working directory, and pass ``PYTHONPATH`` so ``-m airthrow`` resolves.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_NAME = "AirThrow"
SERVICE_NAME = "airthrow"
IS_WIN = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")


# --------------------------------------------------------------------------
# Launch target (frozen exe or from-source venv)
# --------------------------------------------------------------------------
def _repo_dir() -> Path | None:
    """The folder that contains the `airthrow` package (source installs only)."""
    if getattr(sys, "frozen", False):
        return None
    return Path(__file__).resolve().parent.parent


def _launch_target(subcmd: str = "tray", windowless: bool = True):
    """Return (program, args, workdir) that starts AirThrow in `subcmd` mode."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        if windowless and subcmd == "tray":
            tray = exe_dir / ("airthrow-tray.exe" if IS_WIN else "airthrow-tray")
            if tray.exists():
                return str(tray), [], str(exe_dir)
        return sys.executable, [subcmd], str(exe_dir)

    # From source: use the venv's python (pythonw on Windows for no console).
    py = sys.executable
    if IS_WIN and windowless:
        pyw = Path(py).with_name("pythonw.exe")
        if pyw.exists():
            py = str(pyw)
    repo = _repo_dir()
    return py, ["-m", "airthrow", subcmd], str(repo) if repo else os.getcwd()


def _quote(s: str) -> str:
    return f'"{s}"' if " " in s else s


def _command_string(subcmd: str = "tray") -> str:
    prog, args, _ = _launch_target(subcmd)
    return " ".join([_quote(prog), *args]).strip()


# ==========================================================================
# Login autostart
# ==========================================================================
def _win_shortcut_path() -> Path:
    startup = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / \
        "Start Menu" / "Programs" / "Startup"
    return startup / f"{APP_NAME}.lnk"


def _win_enable() -> str:
    import win32com.client  # from pywin32

    lnk = _win_shortcut_path()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    prog, args, workdir = _launch_target("tray")
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(str(lnk))
    shortcut.TargetPath = prog
    shortcut.Arguments = " ".join(args)
    # Working dir = repo so `-m airthrow` resolves for source installs.
    shortcut.WorkingDirectory = workdir
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


def _linux_desktop_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / "airthrow.desktop"


def _linux_enable() -> str:
    path = _linux_desktop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    prog, args, workdir = _launch_target("tray")
    exec_parts = [_quote(prog), *args]
    if _repo_dir():  # source: make -m airthrow resolvable
        exec_parts = ["env", f"PYTHONPATH={workdir}", *exec_parts]
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={' '.join(exec_parts)}\n"
        f"Path={workdir}\n"
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


def enable() -> str:
    if IS_WIN:
        return _win_enable()
    if IS_LINUX:
        return _linux_enable()
    raise RuntimeError(f"autostart not supported on {sys.platform}")


def disable() -> bool:
    if IS_WIN:
        return _win_disable()
    if IS_LINUX:
        return _linux_disable()
    raise RuntimeError(f"autostart not supported on {sys.platform}")


def status() -> bool:
    if IS_WIN:
        return _win_status()
    if IS_LINUX:
        return _linux_status()
    return False


# ==========================================================================
# Background service / daemon
# ==========================================================================
def _systemd_unit_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "systemd" / "user" / f"{SERVICE_NAME}.service"


def _has_systemctl() -> bool:
    from shutil import which
    return which("systemctl") is not None


def _linux_service_install() -> str:
    if not _has_systemctl():
        # No systemd: fall back to the login autostart entry.
        return _linux_enable() + "  (no systemd; used login autostart instead)"

    prog, args, workdir = _launch_target("daemon", windowless=False)
    exec_line = " ".join([_quote(prog), *args])
    env_line = f"Environment=PYTHONPATH={workdir}\n" if _repo_dir() else ""
    unit = (
        "[Unit]\n"
        "Description=AirThrow background agent (receive + gestures)\n"
        "After=graphical-session.target\n\n"
        "[Service]\n"
        f"ExecStart={exec_line}\n"
        f"WorkingDirectory={workdir}\n"
        f"{env_line}"
        "Restart=on-failure\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    path = _systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit, encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now",
                    f"{SERVICE_NAME}.service"], check=False)
    return str(path)


def _linux_service_uninstall() -> bool:
    removed = False
    if _has_systemctl():
        subprocess.run(["systemctl", "--user", "disable", "--now",
                        f"{SERVICE_NAME}.service"], check=False)
    path = _systemd_unit_path()
    if path.exists():
        path.unlink()
        removed = True
        if _has_systemctl():
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    # Also clear any login-autostart fallback.
    removed = _linux_disable() or removed
    return removed


def _linux_service_status() -> str:
    if _has_systemctl():
        r = subprocess.run(["systemctl", "--user", "is-active",
                            f"{SERVICE_NAME}.service"],
                           capture_output=True, text=True)
        state = (r.stdout or r.stderr).strip() or "unknown"
        return f"systemd user service: {state}"
    return f"login autostart: {'on' if _linux_status() else 'off'}"


def _win_service_install() -> str:
    # Start at login (hidden), and launch it now in the background.
    lnk = _win_enable()
    prog, args, workdir = _launch_target("tray")
    try:
        DETACHED = 0x00000008  # DETACHED_PROCESS
        subprocess.Popen([prog, *args], cwd=workdir, close_fds=True,
                         creationflags=DETACHED)
    except Exception:
        pass
    return lnk


def _win_service_uninstall() -> bool:
    return _win_disable()


def _win_service_status() -> str:
    running = False
    try:
        import psutil
        for p in psutil.process_iter(["name", "cmdline"]):
            name = (p.info.get("name") or "").lower()
            cmd = " ".join(p.info.get("cmdline") or []).lower()
            if "airthrow-tray" in name or ("airthrow" in cmd and "tray" in cmd):
                running = True
                break
    except Exception:
        pass
    return (f"login agent: {'on' if _win_status() else 'off'}; "
            f"running now: {'yes' if running else 'no'}")


def service_install() -> str:
    if IS_WIN:
        return _win_service_install()
    if IS_LINUX:
        return _linux_service_install()
    raise RuntimeError(f"service not supported on {sys.platform}")


def service_uninstall() -> bool:
    if IS_WIN:
        return _win_service_uninstall()
    if IS_LINUX:
        return _linux_service_uninstall()
    raise RuntimeError(f"service not supported on {sys.platform}")


def service_status() -> str:
    if IS_WIN:
        return _win_service_status()
    if IS_LINUX:
        return _linux_service_status()
    return "service not supported on this OS"
