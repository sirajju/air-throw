"""AirThrow desktop GUI — a cross-platform control panel (Windows + Linux).

A single Tkinter window to do everything the CLI does without typing commands:
set this device's name, assign the left/right peers (and ping them), pick the
gesture and sensitivity, and Start/Stop the camera + receiver. Status updates
live at the bottom. The camera/gesture loop runs on a worker thread while Tk
owns the main thread; the Huawei-style gradient send border (see
:mod:`airthrow.send_overlay`) fires on every throw, GUI or not.

Tkinter ships with Python and renders natively on both OSes, so the same window
works everywhere and bundles cleanly with PyInstaller.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from . import __version__, send_overlay
from .app import AirThrow, open_with_default_app
from .config import RECEIVED_DIR, SLOTS, Config, local_ip
from .logs import LOG_PATH, get_logger, setup_logging
from .transfer import check_peer

log = get_logger()

# Palette (dark, matches the send border's teal accent).
BG = "#1b1d23"
CARD = "#23262e"
FG = "#e6e8ee"
MUTED = "#9aa0ad"
ACCENT = "#00e5c8"
OK = "#39d353"
WARN = "#ffb020"
ERR = "#ff5d6c"


class GUI:
    def __init__(self, camera_index: int = 0):
        self.config = Config.load()
        self.camera_index = camera_index

        self.app: AirThrow | None = None
        self.worker: threading.Thread | None = None
        self.stop_event: threading.Event | None = None
        self.running = False

        self.root = tk.Tk()
        self.root.title("AirThrow")
        self.root.configure(bg=BG)
        self.root.minsize(460, 640)
        self._build_styles()

        # Tk variables bound to settings.
        self.v_name = tk.StringVar(value=self.config.device_name)
        self.v_gesture = tk.StringVar(value=self.config.gesture_mode)
        self.v_sens = tk.DoubleVar(value=self.config.swipe_min_distance)
        self.v_autoopen = tk.BooleanVar(value=self.config.auto_open)
        self.v_anim = tk.BooleanVar(value=self.config.show_send_animation)
        self.v_preview = tk.BooleanVar(value=False)
        self.v_camera = tk.IntVar(value=camera_index)
        self.v_peer = {
            slot: (tk.StringVar(), tk.StringVar(), tk.StringVar())  # name, ip, port
            for slot in SLOTS
        }
        self.peer_status = {}
        for slot in SLOTS:
            peer = self.config.peers.get(slot)
            n, ip, port = self.v_peer[slot]
            if peer:
                n.set(peer.name); ip.set(peer.ip); port.set(str(peer.port))

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_status()

    # ---- styling -----------------------------------------------------
    def _build_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, fieldbackground=CARD,
                        bordercolor=CARD, font=("Segoe UI", 10))
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Card.TLabel", background=CARD, foreground=FG)
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED)
        style.configure("Head.TLabel", background=BG, foreground=ACCENT,
                        font=("Segoe UI Semibold", 11))
        style.configure("TButton", background="#2d313b", foreground=FG,
                        borderwidth=0, focuscolor=CARD, padding=6)
        style.map("TButton", background=[("active", "#3a3f4b")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#06231f",
                        font=("Segoe UI Semibold", 10), padding=8)
        style.map("Accent.TButton", background=[("active", "#28efd6")])
        style.configure("TEntry", fieldbackground="#2d313b", foreground=FG,
                        insertcolor=FG, borderwidth=0, padding=4)
        style.configure("TCombobox", fieldbackground="#2d313b", foreground=FG,
                        arrowcolor=FG, padding=4)
        style.configure("TCheckbutton", background=CARD, foreground=FG)
        style.map("TCheckbutton", background=[("active", CARD)])
        style.configure("Horizontal.TScale", background=CARD)

    def _card(self, parent, title) -> ttk.Frame:
        ttk.Label(parent, text=title, style="Head.TLabel").pack(
            anchor="w", padx=4, pady=(12, 4))
        frame = ttk.Frame(parent, style="Card.TFrame", padding=12)
        frame.pack(fill="x", padx=2)
        return frame

    # ---- layout ------------------------------------------------------
    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="AirThrow", font=("Segoe UI Semibold", 18),
                  foreground=ACCENT).pack(side="left")
        ttk.Label(header, text=f"v{__version__}", style="Muted.TLabel",
                  foreground=MUTED).pack(side="left", padx=8)

        # This device --------------------------------------------------
        dev = self._card(outer, "This device")
        row = ttk.Frame(dev, style="Card.TFrame"); row.pack(fill="x")
        ttk.Label(row, text="Name", style="Card.TLabel", width=8).pack(side="left")
        ttk.Entry(row, textvariable=self.v_name).pack(side="left", fill="x",
                                                      expand=True, padx=6)
        ttk.Button(row, text="Save", command=self._save_name).pack(side="left")
        self.addr_lbl = ttk.Label(
            dev, text=self._addr_text(), style="Muted.TLabel")
        self.addr_lbl.pack(anchor="w", pady=(8, 0))

        # Peers --------------------------------------------------------
        peers = self._card(outer, "Peers  (who's on your left / right)")
        for slot in SLOTS:
            self._peer_row(peers, slot)

        # Gesture & behaviour -----------------------------------------
        beh = self._card(outer, "Gesture & behaviour")
        grow = ttk.Frame(beh, style="Card.TFrame"); grow.pack(fill="x")
        ttk.Label(grow, text="Gesture", style="Card.TLabel", width=10).pack(side="left")
        cb = ttk.Combobox(grow, textvariable=self.v_gesture, state="readonly",
                          values=("two", "grab", "swipe"), width=10)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._save_settings())
        ttk.Label(grow, text="(two = two-finger swipe)", style="Muted.TLabel").pack(
            side="left")

        srow = ttk.Frame(beh, style="Card.TFrame"); srow.pack(fill="x", pady=(8, 0))
        ttk.Label(srow, text="Sensitivity", style="Card.TLabel", width=10).pack(side="left")
        self.sens_lbl = ttk.Label(srow, text=self._sens_text(), style="Muted.TLabel",
                                  width=16)
        self.sens_lbl.pack(side="right")
        scale = ttk.Scale(srow, from_=0.02, to=0.6, variable=self.v_sens,
                          command=self._on_sens)
        scale.pack(side="left", fill="x", expand=True, padx=6)

        crow = ttk.Frame(beh, style="Card.TFrame"); crow.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(crow, text="Auto-open received images",
                        variable=self.v_autoopen,
                        command=self._save_settings).pack(anchor="w")
        ttk.Checkbutton(crow, text="Show gradient send animation",
                        variable=self.v_anim,
                        command=self._save_settings).pack(anchor="w")

        camrow = ttk.Frame(beh, style="Card.TFrame"); camrow.pack(fill="x", pady=(8, 0))
        ttk.Label(camrow, text="Camera", style="Card.TLabel", width=10).pack(side="left")
        ttk.Spinbox(camrow, from_=0, to=8, width=5, textvariable=self.v_camera).pack(
            side="left")
        ttk.Label(camrow, text="(index; change then Start)", style="Muted.TLabel").pack(
            side="left", padx=6)
        ttk.Button(camrow, text="Test border", command=self._test_border).pack(
            side="right")

        # Run controls -------------------------------------------------
        run = self._card(outer, "Run")
        rrow = ttk.Frame(run, style="Card.TFrame"); rrow.pack(fill="x")
        self.start_btn = ttk.Button(rrow, text="Start", style="Accent.TButton",
                                    command=self._toggle_run)
        self.start_btn.pack(side="left")
        ttk.Checkbutton(rrow, text="Show camera preview", variable=self.v_preview,
                        command=self._preview_changed).pack(side="left", padx=12)

        arow = ttk.Frame(run, style="Card.TFrame"); arow.pack(fill="x", pady=(8, 0))
        ttk.Button(arow, text="Open received folder",
                   command=self._open_received).pack(side="left")
        ttk.Button(arow, text="Open log", command=self._open_log).pack(
            side="left", padx=6)

        # Status bar ---------------------------------------------------
        self.status_lbl = ttk.Label(outer, text="●  Idle", style="Muted.TLabel",
                                    anchor="w")
        self.status_lbl.pack(fill="x", pady=(14, 0))

    def _peer_row(self, parent, slot: str):
        n, ip, port = self.v_peer[slot]
        box = ttk.Frame(parent, style="Card.TFrame"); box.pack(fill="x", pady=4)
        arrow = "◀  left" if slot == "left" else "right  ▶"
        ttk.Label(box, text=arrow, style="Card.TLabel", width=9,
                  foreground=ACCENT).pack(side="left")
        ttk.Entry(box, textvariable=n, width=10).pack(side="left", padx=2)
        ttk.Entry(box, textvariable=ip, width=13).pack(side="left", padx=2)
        ttk.Entry(box, textvariable=port, width=6).pack(side="left", padx=2)
        ttk.Button(box, text="Save", width=5,
                   command=lambda s=slot: self._save_peer(s)).pack(side="left", padx=2)
        dot = ttk.Label(box, text="●", style="Card.TLabel", foreground=MUTED)
        dot.pack(side="left", padx=4)
        self.peer_status[slot] = dot
        ttk.Button(box, text="Ping", width=5,
                   command=lambda s=slot: self._ping(s)).pack(side="left")

    # ---- settings actions -------------------------------------------
    def _addr_text(self) -> str:
        return (f"LAN address:  {local_ip()}:{self.config.listen_port}"
                "   → give this to the other machine")

    def _sens_text(self) -> str:
        return f"{self.v_sens.get():.2f}  (lower = easier)"

    def _on_sens(self, _evt=None):
        self.sens_lbl.configure(text=self._sens_text())

    def _save_name(self):
        name = self.v_name.get().strip()
        if name:
            self.config.device_name = name
            self.config.save()
            self.addr_lbl.configure(text=self._addr_text())
            self._flash_status(f"Device name saved: {name}", OK)

    def _save_settings(self):
        self.config.gesture_mode = self.v_gesture.get()
        self.config.swipe_min_distance = round(self.v_sens.get(), 3)
        self.config.auto_open = self.v_autoopen.get()
        self.config.show_send_animation = self.v_anim.get()
        self.config.save()
        msg = "Settings saved"
        if self.running:
            msg += " — press Stop then Start to apply gesture/sensitivity"
        self._flash_status(msg, OK)

    def _save_peer(self, slot: str):
        n, ip, port = self.v_peer[slot]
        name, ip_v, port_v = n.get().strip(), ip.get().strip(), port.get().strip()
        if not ip_v:
            self._flash_status(f"{slot}: enter an IP address first", WARN)
            return
        try:
            port_i = int(port_v) if port_v else self.config.listen_port
        except ValueError:
            self._flash_status(f"{slot}: port must be a number", WARN)
            return
        self.config.set_peer(slot, name or ip_v, ip_v, port_i)
        self._flash_status(f"{slot} peer saved: {name or ip_v} @ {ip_v}:{port_i}", OK)

    def _ping(self, slot: str):
        n, ip, port = self.v_peer[slot]
        ip_v = ip.get().strip()
        if not ip_v:
            return
        try:
            port_i = int(port.get().strip() or self.config.listen_port)
        except ValueError:
            return
        self.peer_status[slot].configure(foreground=WARN)  # pinging

        def work():
            ok = check_peer(f"http://{ip_v}:{port_i}")
            self.root.after(0, lambda: self.peer_status[slot].configure(
                foreground=OK if ok else ERR))
            self.root.after(0, lambda: self._flash_status(
                f"{slot}: {'reachable' if ok else 'NOT reachable'}",
                OK if ok else ERR))

        threading.Thread(target=work, daemon=True).start()

    def _test_border(self):
        send_overlay.flash(None)  # frame the whole screen as a preview
        self._flash_status("Send border preview", ACCENT)

    # ---- run control -------------------------------------------------
    def _toggle_run(self):
        if self.running:
            self._stop()
        else:
            self._start()

    def _start(self):
        self._save_settings()
        self.config = Config.load()
        self.camera_index = self.v_camera.get()
        self.app = AirThrow(self.config, camera_index=self.camera_index)
        self.stop_event = threading.Event()
        self.worker = threading.Thread(
            target=self.app.start_background,
            args=(self.stop_event, lambda: self.v_preview.get(),
                  self._preview_hidden),
            name="airthrow-camera", daemon=True)
        self.worker.start()
        self.running = True
        self.start_btn.configure(text="Stop")
        self._flash_status("Running — camera + receiver started", OK)

    def _stop(self):
        if self.stop_event:
            self.stop_event.set()
        if self.worker:
            self.worker.join(timeout=3)
        self.running = False
        self.app = None
        self.start_btn.configure(text="Start")
        self._flash_status("Stopped", MUTED)

    def _preview_changed(self):
        pass  # value is read live by the camera loop via v_preview.get()

    def _preview_hidden(self):
        # Called from the camera thread when the user pressed q in the preview.
        self.root.after(0, lambda: self.v_preview.set(False))

    def _open_received(self):
        RECEIVED_DIR.mkdir(parents=True, exist_ok=True)
        open_with_default_app(RECEIVED_DIR)

    def _open_log(self):
        if LOG_PATH.exists():
            open_with_default_app(LOG_PATH)
        else:
            self._flash_status("No log yet — Start first", WARN)

    # ---- status ------------------------------------------------------
    def _flash_status(self, text: str, color=FG):
        self.status_lbl.configure(text=f"●  {text}", foreground=color)

    def _poll_status(self):
        if self.running and self.app:
            self.status_lbl.configure(text=f"●  {self.app.status}",
                                      foreground=ACCENT)
        self.root.after(500, self._poll_status)

    def _on_close(self):
        if self.running:
            self._stop()
        self.root.destroy()

    def run(self):
        log.info("GUI started")
        self.root.mainloop()
        log.info("GUI exited")


def run_gui(camera_index: int = 0):
    setup_logging(debug=False)
    GUI(camera_index=camera_index).run()
