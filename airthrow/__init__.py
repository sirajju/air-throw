"""AirThrow — throw files across the air to other computers with a hand gesture.

Stage 1 MVP: images only, peers added manually by IP, swipe left/right to a
computer assigned to that direction.
"""

__version__ = "0.1.0"

DEFAULT_PORT = 8720
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".heic",
}
