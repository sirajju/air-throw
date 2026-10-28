# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AirThrow — builds two single-file executables:

  * airthrow       — console CLI (run/info/tray/...), shows a terminal
  * airthrow-tray  — windowed (no console), for the background tray app

The same spec is used on Windows (.exe) and Linux. PyInstaller cannot cross-
compile: run it on the OS you want binaries for.

Tricky dependencies collected explicitly:
  * mediapipe  — ships .tflite/.binarypb models + native libs as data
  * cv2        — native libs
  * matplotlib — imported internally by mediapipe (needs its mpl-data)
  * uvicorn    — imports loop/protocol workers by string name
  * pystray/PIL — tray backend + icon rendering
  * win32com   — used for the Windows autostart shortcut
"""

import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# Our own bundled assets (the HandLandmarker model used by gestures.py).
datas += [("airthrow/assets/hand_landmarker.task", "airthrow/assets")]

# Pull everything (py modules, dynamic libs, data files) for the hard ones.
for pkg in ("mediapipe", "cv2", "matplotlib", "pystray", "PIL"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# uvicorn + its optional "standard" workers are imported by string name.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "uvicorn.lifespan.on",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "multipart",
    "python_multipart",
]

# Windows autostart shortcut uses win32com (part of pywin32).
if sys.platform.startswith("win"):
    hiddenimports += ["win32com", "win32com.client", "pythoncom", "pywintypes",
                      "win32timezone"]

block_cipher = None


def make_analysis(entry):
    return Analysis(
        [entry],
        pathex=[],
        binaries=binaries,
        datas=datas,
        hiddenimports=hiddenimports,
        hookspath=[],
        runtime_hooks=[],
        excludes=["tkinter"],  # not used; keeps the binary smaller
        cipher=block_cipher,
        noarchive=False,
    )


# ---- console CLI executable ------------------------------------------------
a_cli = make_analysis("run_airthrow.py")
pyz_cli = PYZ(a_cli.pure, a_cli.zipped_data, cipher=block_cipher)
exe_cli = EXE(
    pyz_cli, a_cli.scripts, a_cli.binaries, a_cli.datas, [],
    name="airthrow",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True,
    disable_windowed_traceback=False, target_arch=None,
    codesign_identity=None, entitlements_file=None,
)

# ---- windowed tray executable (no console) ---------------------------------
a_tray = make_analysis("run_tray.py")
pyz_tray = PYZ(a_tray.pure, a_tray.zipped_data, cipher=block_cipher)
exe_tray = EXE(
    pyz_tray, a_tray.scripts, a_tray.binaries, a_tray.datas, [],
    name="airthrow-tray",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False,
    disable_windowed_traceback=False, target_arch=None,
    codesign_identity=None, entitlements_file=None,
)
