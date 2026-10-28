"""Windowed (no-console) entry point for the AirThrow desktop GUI.

PyInstaller builds this as `airthrow-gui` with console=False, so double-clicking
it opens the control-panel window with no terminal behind it.
"""

import multiprocessing

from airthrow.gui import run_gui

if __name__ == "__main__":
    multiprocessing.freeze_support()
    run_gui()
