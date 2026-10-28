"""AirThrow — throw files across the air to other computers with a hand gesture.

Stage 1 MVP: images only, peers added manually by IP, swipe left/right to a
computer assigned to that direction.
"""

import os
import sys

__version__ = "0.1.0"

DEFAULT_PORT = 8720


def asset_path(name: str) -> str:
    """Absolute path to a bundled asset, both from source and when frozen.

    PyInstaller unpacks bundled data under sys._MEIPASS at runtime; from source
    the assets live next to this package.
    """
    if getattr(sys, "frozen", False):
        base = os.path.join(sys._MEIPASS, "airthrow", "assets")  # type: ignore[attr-defined]
    else:
        base = os.path.join(os.path.dirname(__file__), "assets")
    return os.path.join(base, name)
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".heic",
}
