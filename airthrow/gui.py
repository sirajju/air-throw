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

import math
import threading
import tkinter as tk
from tkinter import ttk

from . import DEFAULT_PORT, __version__, send_overlay
from .send_overlay import STYLE_NAMES
from .app import AirThrow, open_with_default_app
from .config import RECEIVED_DIR, SLOTS, Config, local_ip
from .logs import LOG_PATH, get_logger, setup_logging
from .transfer import check_peer

log = get_logger()

# Palette — light, Apple-like: soft grey canvas, white cards, hairline borders,
# system-blue accent, lots of whitespace.
BG = "#f5f5f7"        # window background
CARD = "#ffffff"      # card surface
BORDER = "#e4e4e9"    # hairline border
FG = "#1d1d1f"        # primary text (near-black)
MUTED = "#86868b"     # secondary text
ACCENT = "#0071e3"    # system blue
ACCENT_HOVER = "#0077ed"
FIELD = "#f2f2f5"     # input / secondary-button fill
FIELD_HOVER = "#e7e7ec"
OK = "#1f9d4d"
WARN = "#c77700"
ERR = "#d7263d"
FONT = "Segoe UI"     # clean system sans (falls back on Linux)


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
        self.root.minsize(440, 560)
        self.root.geometry("480x720")
        self._build_styles()

        # Tk variables bound to settings.
        self.v_name = tk.StringVar(value=self.config.device_name)
        self.v_gesture = tk.StringVar(value=self.config.gesture_mode)
        self.v_sens = tk.DoubleVar(value=self.config.swipe_min_distance)
        self.v_autoopen = tk.BooleanVar(value=self.config.auto_open)
        self.v_anim = tk.BooleanVar(value=self.config.show_send_animation)
        self.v_style = tk.StringVar(value=self.config.send_animation_style)
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
        style.configure(".", background=BG, foreground=FG, fieldbackground=FIELD,
                        bordercolor=BORDER, font=(FONT, 10))
        # Cards: white surface, hairline border.
        style.configure("Card.TFrame", background=CARD, borderwidth=1,
                        relief="solid", bordercolor=BORDER)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Card.TLabel", background=CARD, foreground=FG)
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED)
        style.configure("Head.TLabel", background=BG, foreground=MUTED,
                        font=(FONT, 10, "bold"))
        # Secondary button: light grey pill.
        style.configure("TButton", background=FIELD, foreground=FG,
                        borderwidth=0, focuscolor=CARD, padding=(12, 7),
                        font=(FONT, 10))
        style.map("TButton", background=[("active", FIELD_HOVER),
                                         ("pressed", FIELD_HOVER)])
        # Primary button: system blue.
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                        font=(FONT, 10, "bold"), padding=(16, 9), borderwidth=0)
        style.map("Accent.TButton", background=[("active", ACCENT_HOVER),
                                                ("pressed", ACCENT_HOVER)])
        style.configure("TEntry", fieldbackground=FIELD, foreground=FG,
                        insertcolor=FG, borderwidth=0, padding=6)
        style.configure("TSpinbox", fieldbackground=FIELD, foreground=FG,
                        arrowcolor=FG, borderwidth=0, padding=4)
        style.configure("TCombobox", fieldbackground=FIELD, foreground=FG,
                        arrowcolor=MUTED, borderwidth=0, padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)])
        style.configure("TCheckbutton", background=CARD, foreground=FG)
        style.map("TCheckbutton", background=[("active", CARD)])
        style.configure("Horizontal.TScale", background=CARD, troughcolor=FIELD,
                        borderwidth=0)
        style.configure("Vertical.TScrollbar", background=BORDER, troughcolor=BG,
                        borderwidth=0, arrowcolor=MUTED)
        style.map("Vertical.TScrollbar", background=[("active", MUTED)])

    def _card(self, parent, title) -> ttk.Frame:
        ttk.Label(parent, text=title.upper(), style="Head.TLabel").pack(
            anchor="w", padx=6, pady=(16, 6))
        frame = ttk.Frame(parent, style="Card.TFrame", padding=16)
        frame.pack(fill="x")
        return frame

    # ---- layout ------------------------------------------------------
    def _build_ui(self):
        # Pinned status bar at the bottom (outside the scroll area).
        bar = tk.Frame(self.root, bg="#ffffff", highlightthickness=1,
                       highlightbackground=BORDER)
        bar.pack(side="bottom", fill="x")
        self.status_lbl = tk.Label(bar, text="  ●  Idle", bg="#ffffff",
                                   fg=MUTED, anchor="w", font=(FONT, 10),
                                   padx=10, pady=8)
        self.status_lbl.pack(fill="x")

        # Scrollable body.
        body = tk.Frame(self.root, bg=BG)
        body.pack(side="top", fill="both", expand=True)
        canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        outer = ttk.Frame(canvas, padding=(18, 4, 18, 18))
        self._scroll_window = canvas.create_window((0, 0), window=outer,
                                                   anchor="nw")
        self._scroll_canvas = canvas

        outer.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(self._scroll_window,
                                                   width=e.width))
        self._bind_mousewheel(canvas)

        # Header -------------------------------------------------------
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(10, 2))
        ttk.Label(header, text="AirThrow", font=(FONT, 22, "bold"),
                  foreground=FG).pack(side="left")
        ttk.Label(header, text=f"v{__version__}", style="TLabel",
                  foreground=MUTED, font=(FONT, 11)).pack(side="left", padx=8,
                                                          pady=(10, 0))

        # This device --------------------------------------------------
        dev = self._card(outer, "This device")
        row = ttk.Frame(dev, style="Card.TFrame"); row.pack(fill="x")
        ttk.Label(row, text="Name", style="Card.TLabel", width=8).pack(side="left")
        ttk.Entry(row, textvariable=self.v_name).pack(side="left", fill="x",
                                                      expand=True, padx=6)
        ttk.Button(row, text="Save", command=self._save_name).pack(side="left")
        self.addr_lbl = ttk.Label(
            dev, text=self._addr_text(), style="Muted.TLabel", wraplength=400)
        self.addr_lbl.pack(anchor="w", pady=(8, 0))

        # Peers --------------------------------------------------------
        peers = self._card(outer, "Peers  (who's on your left / right)")
        for slot in SLOTS:
            self._peer_row(peers, slot)

        # Extra devices (threshold+ enables the picker) ----------------
        dev2 = self._card(outer, "More devices  (picker appears at the threshold)")
        self.dev_list = tk.Listbox(dev2, height=4, bg=FIELD, fg=FG,
                                   borderwidth=0, highlightthickness=1,
                                   highlightbackground=BORDER, relief="flat",
                                   selectbackground=ACCENT, selectforeground="#ffffff",
                                   activestyle="none", font=(FONT, 10))
        self.dev_list.pack(fill="x")
        addrow = ttk.Frame(dev2, style="Card.TFrame"); addrow.pack(fill="x", pady=(6, 0))
        self.v_dev_name = tk.StringVar(); self.v_dev_ip = tk.StringVar()
        self.v_dev_port = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(addrow, textvariable=self.v_dev_name, width=10).pack(side="left", padx=2)
        ttk.Entry(addrow, textvariable=self.v_dev_ip, width=13).pack(side="left", padx=2)
        ttk.Entry(addrow, textvariable=self.v_dev_port, width=6).pack(side="left", padx=2)
        ttk.Button(addrow, text="Add", width=5, command=self._add_device).pack(side="left", padx=2)
        ttk.Button(addrow, text="Remove", command=self._remove_device).pack(side="left")
        ttk.Button(addrow, text="Preview picker", command=self._preview_picker).pack(side="right")

        trow = ttk.Frame(dev2, style="Card.TFrame"); trow.pack(fill="x", pady=(6, 0))
        ttk.Label(trow, text="Show picker when devices ≥", style="Card.TLabel").pack(side="left")
        self.v_threshold = tk.IntVar(value=self.config.picker_threshold)
        ttk.Spinbox(trow, from_=2, to=50, width=4, textvariable=self.v_threshold,
                    command=self._save_threshold).pack(side="left", padx=6)
        self._refresh_devices()

        # Canvas map (for the 'canvas' gesture) -----------------------
        self._build_canvas_card(outer)

        # Gesture & behaviour -----------------------------------------
        beh = self._card(outer, "Gesture & behaviour")
        grow = ttk.Frame(beh, style="Card.TFrame"); grow.pack(fill="x")
        ttk.Label(grow, text="Gesture", style="Card.TLabel", width=10).pack(side="left")
        cb = ttk.Combobox(grow, textvariable=self.v_gesture, state="readonly",
                          values=("swipe", "palm", "two", "grab", "canvas"),
                          width=10)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._save_settings())
        ttk.Label(grow, text="(swipe = flick · canvas = map)",
                  style="Muted.TLabel").pack(side="left")

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

        strow = ttk.Frame(beh, style="Card.TFrame"); strow.pack(fill="x", pady=(8, 0))
        ttk.Label(strow, text="Border style", style="Card.TLabel", width=10).pack(side="left")
        sb = ttk.Combobox(strow, textvariable=self.v_style, state="readonly",
                          values=STYLE_NAMES, width=10)
        sb.pack(side="left", padx=6)
        sb.bind("<<ComboboxSelected>>",
                lambda e: (self._save_settings(), self._test_border()))
        ttk.Button(strow, text="Preview this style",
                   command=self._test_border).pack(side="left", padx=6)

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

    def _bind_mousewheel(self, canvas):
        def on_wheel(e):
            delta = e.delta
            canvas.yview_scroll(int(-delta / 120) if delta else 0, "units")
        canvas.bind_all("<MouseWheel>", on_wheel)             # Windows / macOS
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

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
        self.config.send_animation_style = self.v_style.get()
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
            port_i = int(port_v) if port_v else DEFAULT_PORT
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
            port_i = int(port.get().strip() or DEFAULT_PORT)
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
        style = self.v_style.get()
        send_overlay.flash(None, style=style)  # frame the whole screen as a preview
        self._flash_status(f"Border preview: {style}", ACCENT)

    # ---- extra devices ----------------------------------------------
    def _refresh_devices(self):
        self.dev_list.delete(0, tk.END)
        for d in self.config.devices:
            self.dev_list.insert(tk.END, f"{d.name}   {d.ip}:{d.port}")
        n = len(self.config.all_devices())
        tag = ("picker ON" if self.config.use_picker()
               else f"need {self.config.picker_threshold}+ for picker")
        self._flash_status(f"{n} target(s) — {tag}", MUTED)

    def _save_threshold(self):
        try:
            self.config.picker_threshold = max(2, int(self.v_threshold.get()))
        except (ValueError, tk.TclError):
            return
        self.config.save()
        self._refresh_devices()

    # ---- canvas map editor ------------------------------------------
    def _build_canvas_card(self, outer):
        card = self._card(outer, "Canvas map  (place each device by its direction)")
        self.CW, self.CH = 320, 220
        self.map_cv = tk.Canvas(card, width=self.CW, height=self.CH, bg=FIELD,
                                highlightthickness=1, highlightbackground=BORDER)
        self.map_cv.pack()
        self.map_cv.bind("<Button-1>", self._map_press)
        self.map_cv.bind("<B1-Motion>", self._map_drag)
        self.map_cv.bind("<ButtonRelease-1>", self._map_release)
        ttk.Label(card, text="Drag each dot to where that computer sits (centre = "
                  "you). Then set gesture to 'canvas' and reach toward it.",
                  style="Muted.TLabel", wraplength=320).pack(anchor="w", pady=(4, 0))
        self._drag_name = None
        self._dot_hit = []
        self._redraw_map()

    def _map_target_pos(self, p, i, n):
        if p.x is not None and p.y is not None:
            return p.x, p.y
        ang = -math.pi / 2 + i * (2 * math.pi / max(1, n))
        return 0.5 + 0.32 * math.cos(ang), 0.5 + 0.32 * math.sin(ang)

    def _redraw_map(self):
        c = self.map_cv
        c.delete("all")
        cx, cy = self.CW / 2, self.CH / 2
        for r in (0.18, 0.32):
            rr = r * min(self.CW, self.CH)
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline="#dcdce2")
        targets = self.config.all_devices()
        self._dot_hit = []
        for i, p in enumerate(targets):
            x, y = self._map_target_pos(p, i, len(targets))
            px, py = x * self.CW, y * self.CH
            c.create_line(cx, cy, px, py, fill="#c7c7cf")
            c.create_oval(px - 9, py - 9, px + 9, py + 9, fill=ACCENT, outline="")
            c.create_text(px, py - 14, text=p.name, fill=FG,
                          font=(FONT, 9, "bold"))
            self._dot_hit.append((p.name, px, py))
        c.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, fill="#ff2d55", outline="#fff")
        c.create_text(cx, cy + 18, text="YOU", fill="#ff2d55", font=(FONT, 9, "bold"))

    def _map_press(self, e):
        for name, px, py in self._dot_hit:
            if (e.x - px) ** 2 + (e.y - py) ** 2 <= 196:  # within 14px
                self._drag_name = name
                return

    def _map_drag(self, e):
        if not self._drag_name:
            return
        x = min(max(e.x, 6), self.CW - 6) / self.CW
        y = min(max(e.y, 6), self.CH - 6) / self.CH
        for p in list(self.config.peers.values()) + self.config.devices:
            if p.name == self._drag_name:
                p.x, p.y = x, y
        self._redraw_map()

    def _map_release(self, e):
        if self._drag_name:
            self.config.save()
            self._drag_name = None
            self._flash_status("Canvas positions saved", OK)

    def _add_device(self):
        name = self.v_dev_name.get().strip()
        ip = self.v_dev_ip.get().strip()
        if not name or not ip:
            self._flash_status("Enter a name and IP for the device", WARN)
            return
        try:
            port = int(self.v_dev_port.get().strip() or DEFAULT_PORT)
        except ValueError:
            self._flash_status("Port must be a number", WARN)
            return
        self.config.add_device(name, ip, port)
        self.v_dev_name.set(""); self.v_dev_ip.set("")
        self.v_dev_port.set(str(DEFAULT_PORT))
        self._refresh_devices()

    def _remove_device(self):
        sel = self.dev_list.curselection()
        if not sel:
            self._flash_status("Select a device in the list to remove", WARN)
            return
        name = self.config.devices[sel[0]].name
        self.config.remove_device(name)
        self._refresh_devices()

    def _preview_picker(self):
        names = [d.name for d in self.config.all_devices()]
        if len(names) < 2:
            names = ["Living-Room-PC", "Studio-PC", "Salah-Laptop", "Bedroom-PC"]
        send_overlay.selector_show(names, title="Throw to…",
                                   style=self.v_style.get())
        self._flash_status("Picker preview (closes shortly)", ACCENT)
        # Cycle the highlight, then close — purely a visual demo.
        def step(i=0):
            if i < len(names) * 2:
                send_overlay.selector_set(i % len(names))
                self.root.after(650, lambda: step(i + 1))
            else:
                self.root.after(400, send_overlay.selector_hide)
        step()

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
        if hasattr(self, "status_lbl"):  # may fire during UI construction
            self.status_lbl.configure(text=f"  ●  {text}", fg=color)

    def _poll_status(self):
        if self.running and self.app:
            self.status_lbl.configure(text=f"  ●  {self.app.status}",
                                      fg=ACCENT)
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
