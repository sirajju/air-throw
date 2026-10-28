"""System-tray background app.

Runs AirThrow with no console/preview window by default: a tray icon with
Status, a Show-preview toggle, Open-received-folder, and Quit. The camera +
gesture loop runs in a worker thread; the tray icon owns the main thread.
"""

from __future__ import annotations

import threading

import pystray
from PIL import Image, ImageDraw

from .app import AirThrow, open_with_default_app
from .config import RECEIVED_DIR, Config
from .logs import get_logger, setup_logging

log = get_logger()


def _icon_image() -> Image.Image:
    """A small paper-plane / throw glyph for the tray."""
    img = Image.new("RGB", (64, 64), (24, 24, 28))
    d = ImageDraw.Draw(img)
    d.polygon([(8, 54), (56, 32), (8, 10), (20, 32)], fill=(0, 200, 180))
    d.line([(20, 32), (56, 32)], fill=(24, 24, 28), width=2)
    return img


class Tray:
    def __init__(self, camera_index: int = 0):
        self.config = Config.load()
        self.app = AirThrow(self.config, camera_index=camera_index)
        self.preview_on = False
        self.stop_event = threading.Event()
        self.icon: pystray.Icon | None = None

    # ---- menu --------------------------------------------------------
    def _menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(lambda item: self.app.status, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show preview", self._toggle_preview,
                             checked=lambda item: self.preview_on),
            pystray.MenuItem("Open received folder", self._open_received),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )

    def _toggle_preview(self, icon, item):
        self.preview_on = not self.preview_on

    def _hide_preview(self):
        """Called when the user presses q in the preview window."""
        self.preview_on = False
        if self.icon:
            self.icon.update_menu()

    def _open_received(self, icon, item):
        RECEIVED_DIR.mkdir(parents=True, exist_ok=True)
        open_with_default_app(RECEIVED_DIR)

    def _quit(self, icon, item):
        self.stop_event.set()
        if self.icon:
            self.icon.stop()

    # ---- run ---------------------------------------------------------
    def run(self):
        worker = threading.Thread(
            target=self.app.start_background,
            args=(self.stop_event, lambda: self.preview_on, self._hide_preview),
            name="airthrow-camera", daemon=True,
        )
        worker.start()

        self.icon = pystray.Icon("AirThrow", _icon_image(), "AirThrow",
                                 menu=self._menu())
        log.info("tray started")
        self.icon.run()  # blocks until _quit stops it

        self.stop_event.set()
        worker.join(timeout=3)
        log.info("tray exited")


def run_tray(camera_index: int = 0):
    setup_logging(debug=False)
    Tray(camera_index=camera_index).run()
