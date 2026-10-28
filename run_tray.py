"""Windowed (no-console) entry point for the background tray app.

PyInstaller builds this as `airthrow-tray` with console=False, so launching it
(e.g. from autostart) shows only the tray icon, no terminal window.
"""

import multiprocessing

from airthrow.tray import run_tray

if __name__ == "__main__":
    multiprocessing.freeze_support()
    run_tray()
