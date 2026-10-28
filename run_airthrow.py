"""Frozen-executable entry point.

PyInstaller bundles this file. It just forwards to the normal CLI so the exe
behaves exactly like `python -m airthrow` (run / info / set-peer / ...).
"""

import multiprocessing

from airthrow.__main__ import main

if __name__ == "__main__":
    # Required so PyInstaller one-file builds don't re-spawn the whole app when
    # any dependency uses multiprocessing.
    multiprocessing.freeze_support()
    main()
