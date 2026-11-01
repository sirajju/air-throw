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
    def __init__(self, camera_index: int = 0, camera_on: bool = False):
        self.config = Config.load()
        self.camera_index = camera_index
        self.camera_on = camera_on
        self.preview_on = False
        self.stop_event = threading.Event()
        self.camera_stop = threading.Event()
        self.camera_worker: threading.Thread | None = None
        self.app = AirThrow(self.config, camera_index=camera_index)
        self.icon: pystray.Icon | None = None
        self.hk = None

    # ---- menu --------------------------------------------------------
    def _menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(lambda item: self._status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Enable camera gestures", self._toggle_camera,
                             checked=lambda item: self.camera_on),
            pystray.MenuItem("Show preview", self._toggle_preview,
                             checked=lambda item: self.preview_on,
                             enabled=lambda item: self.camera_on),
            pystray.MenuItem("Open received folder", self._open_received),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )

    def _status_text(self) -> str:
        if self.camera_on:
            return self.app.status
        return f"Receiver active (port {self.config.listen_port}, webcam off)"

    def _toggle_camera(self, icon, item):
        self.camera_on = not self.camera_on
        if self.camera_on:
            self._start_camera()
        else:
            self._stop_camera()
        if self.icon:
            self.icon.update_menu()

    def _start_camera(self):
        self.camera_stop.clear()
        self.app._stop = self.camera_stop
        self.camera_worker = threading.Thread(
            target=self.app._camera_phase,
            name="airthrow-camera", daemon=True,
        )
        self.camera_worker.start()

    def _stop_camera(self):
        self.camera_stop.set()
        self.preview_on = False
        if self.camera_worker:
            self.camera_worker.join(timeout=2)
            self.camera_worker = None

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
        if self.hk:
            self.hk.stop()
        self.app.cancel_send_session()
        self._stop_camera()
        if self.icon:
            self.icon.stop()

    # ---- run ---------------------------------------------------------
    def run(self):
        self.app._serve()
        self.app._preview_fn = lambda: self.preview_on
        self.app._on_preview_key = self._hide_preview
        if self.camera_on:
            self._start_camera()

        if self.config.hotkey_enabled:
            try:
                from .hotkey import HotkeyListener

                self.hk = HotkeyListener(callback=self.app.trigger_send_session,
                                         hotkey_str=self.config.hotkey)
                self.hk.start()
            except Exception as exc:
                log.warning("Could not start global shortcut in tray: %s", exc)

        self.icon = pystray.Icon("AirThrow", _icon_image(), "AirThrow",
                                 menu=self._menu())
        mode = "camera on" if self.camera_on else "receiver-only, webcam off"
        log.info("tray started (%s)", mode)
        self.icon.run()  # blocks until _quit stops it

        self.stop_event.set()
        if self.hk:
            self.hk.stop()
        self.app.cancel_send_session()
        self._stop_camera()
        log.info("tray exited")


def run_tray(camera_index: int = 0, camera_on: bool = False):
    setup_logging(debug=False)
    Tray(camera_index=camera_index, camera_on=camera_on).run()
