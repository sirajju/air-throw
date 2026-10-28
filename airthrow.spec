# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AirThrow — builds a single-file executable.

The same spec is used on Windows (-> airthrow.exe) and Linux (-> airthrow).
PyInstaller cannot cross-compile: run it on the OS you want a binary for.

The tricky dependencies are collected explicitly:
  * mediapipe  — ships .tflite/.binarypb model files + native libs as data
  * cv2        — native libs
  * uvicorn    — imports its loop/protocol workers dynamically (hidden imports)
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# Pull everything (py modules, dynamic libs, data files) for the hard ones.
# matplotlib is required: mediapipe imports it internally (drawing_utils) and
# needs its bundled mpl-data, so it must be collected, not excluded.
for pkg in ("mediapipe", "cv2", "matplotlib"):
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

block_cipher = None

a = Analysis(
    ["run_airthrow.py"],
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

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="airthrow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,            # it's a CLI; keep the console for output
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
