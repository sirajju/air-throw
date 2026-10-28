# AirThrow

Throw an image across the air to another computer with a **hand gesture** —
swipe left to send it to the PC on your left, swipe right for the one on your
right. Like Huawei's "throw to device", but PC-to-PC over your local network,
cross-platform (Windows + Linux).

> **Stage 1 (this version):** images only · peers added manually by IP ·
> swipe left / right · the image is whatever is open in your foreground window.

## How it works

Four pieces work together:

1. **Receiver** — each PC runs a tiny HTTP server, so it can accept throws.
2. **Gesture engine** — your webcam + MediaPipe Hands watch for a fast swipe
   left or right.
3. **Foreground image** — AirThrow reads the image file open in whatever window
   you last had focused (via that window's process).
4. **Transfer** — a swipe POSTs that image to the peer assigned to the swipe's
   direction.

## Install

Two ways to run AirThrow: a **standalone executable** (no Python needed) or
**from source**.

### Option A — standalone executable

Build a single-file executable for your OS (PyInstaller cannot cross-compile, so
each binary is built on its own OS):

Each build produces **three** executables: `airthrow` (console CLI),
`airthrow-gui` (windowed desktop control panel) and `airthrow-tray` (windowed,
no console — the background tray app).

**Windows** → `dist\airthrow.exe` + `dist\airthrow-gui.exe` + `dist\airthrow-tray.exe`
```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

**Linux** → `dist/airthrow` + `dist/airthrow-gui` + `dist/airthrow-tray` (run on a Linux machine)
```bash
./build-linux.sh
```

**Linux binary from Windows/macOS, via Docker** (no Linux box needed):
```bash
docker build -f Dockerfile.linux -t airthrow-linux .
docker run --rm -v "${PWD}/dist:/out" airthrow-linux   # -> ./dist/airthrow
```

Then use the binary exactly like the commands below, e.g.
`airthrow.exe info` / `./airthrow run`.

### Option B — from source

```bash
pip install -r requirements.txt
python -m airthrow info
```

**Sending** needs a **webcam** (that's how you gesture). **Receiving** does not —
a machine with no camera runs in *receive-only* mode and can still accept throws.
On **Linux**, gesture/foreground detection also needs `xdotool`
(`sudo apt install xdotool`).

Verify the hand model loads on a machine (no camera required):

```bash
python -m airthrow selftest    # or:  airthrow selftest
# OK: hand model loaded and processed a frame (direction=None).
```

## Setup (do this on BOTH machines)

Find each machine's address:

```bash
python -m airthrow info
#   LAN address : 192.168.0.216:8720   <- give this to the OTHER machine
```

Optionally name the machine:

```bash
python -m airthrow set-name "Living-Room-PC"
```

Then tell each machine who is to its left and right. On **Machine A**:

```bash
python -m airthrow set-peer right "Studio-PC" 192.168.0.217
python -m airthrow peers      # review
python -m airthrow ping right # confirm it's reachable (start it first)
```

`set-peer <slot> <name> <ip> [port]` where `<slot>` is `left` or `right`.

### More than two devices

Left/right is the fast path for two peers. For **three or more**, add extra named
devices and AirThrow switches to an **on-screen picker**:

```bash
python -m airthrow add-device "Salah-Laptop" 192.168.0.40
python -m airthrow add-device "Bedroom-PC"   192.168.0.41
python -m airthrow devices         # list all targets; shows if the picker is on
python -m airthrow preview-select  # simulate the picker (no send) to see it
```

Once there are 3+ targets, a throw gesture **opens a gradient-bordered picker**
listing every device. **Swipe** to move the highlight, then **hold** (stop
swiping for ~1.8 s) and it sends to the highlighted device. The box stays put
while you choose — a flickery gesture won't make it blink, because the dwell
timer resets on each swipe (built-in debounce) and the box only closes when it
confirms.

## Desktop GUI (Windows + Linux)

Prefer a window to the command line? Open the **control panel** — no commands to
type:

```bash
python -m airthrow gui       # or launch airthrow-gui(.exe) directly (no console)
```

One window to set this device's **name**, assign the **left/right peers** (with a
**Ping** button each), pick the **gesture** and **sensitivity**, toggle
auto-open and the send animation, and **Start/Stop** the camera + receiver. A
live status line at the bottom shows every throw and receive. "Show camera
preview" pops the mirrored webcam view on demand; **Test border** previews the
send animation.

## Use

On every machine that should send or receive, either open the **GUI** above and
press **Start**, or from the terminal:

```bash
python -m airthrow run
```

A mirrored webcam window opens showing your left/right peers and the currently
open image. Then:

1. Open an image in any viewer (Photos, your browser, IrfanView, `eog`, …).
2. Make the **gesture** (see below) toward **left** or **right**.
3. An **animated gradient border** flashes around the image's window — the same
   Huawei-style confirmation that it's being flung — and the open image flies to
   that direction's peer and lands in its `~/.airthrow/Received` folder, where it
   **opens automatically** in the default viewer (turn off with
   `set-auto-open off`). Turn the border off with `set-animation off`.

On the **receiving** machine, a green **"it landed"** border flashes around the
screen as each file arrives, so you get the same confirmation on both ends.

**Preview the border without sending anything.** No second machine or open image
needed — just watch it on screen:

```bash
python -m airthrow preview            # cycle through every style
python -m airthrow preview ocean      # just one style
python -m airthrow preview --window   # frame the focused window, not full screen
python -m airthrow set-animation-style aurora   # pick your favourite
```

Styles: **aurora** (teal→blue→violet→pink), **rainbow**, **ocean**, **sunset**,
**mono**. In the GUI, the **Border style** dropdown previews each as you select
it, and **Preview this style** replays the current one.

Press `q` or `Esc` to quit.

### Gestures

Three modes (`set-gesture two|grab|swipe`, default **two**):

- **two** (two-finger swipe, default): hold up **two fingers** (index + middle,
  the "peace" sign) and **flick** left or right. The pose must be held through
  the whole motion, so stray hand movement won't fire — reliable and quick.
- **grab** (Huawei-style): make a **fist** to *pick up* the open image (overlay
  shows "Picked up …"), move toward the target, then **open your hand** to
  release/send. Opening without moving far enough cancels.
- **swipe**: just **flick** your whole hand left or right. Simplest, but fires
  on any fast motion.

**Which way?** The view is mirrored, so moving your hand **to your right** sends
to the **right** peer and **to your left** sends to the **left** peer. If a
gesture does nothing, check the `--debug` overlay: the progress bar must reach
100%. Too touchy or too hard? Tune with `set-sensitivity <d>`.

## Run in the background (tray app)

Instead of the foreground preview, run AirThrow as a **system-tray app** — no
console, no window unless you ask for it:

```bash
airthrow tray            # or launch airthrow-tray(.exe) directly (no console)
```

The tray icon menu has **Status**, **Show preview** (toggle the camera window),
**Open received folder**, and **Quit**. Start it automatically on login with:

```bash
airthrow autostart on    # off / status also available
```

Windows adds a Startup shortcut; Linux adds `~/.config/autostart/airthrow.desktop`.
Autostart launches the windowed `airthrow-tray` so nothing pops up but the icon.

## Commands

| Command | What it does |
|---|---|
| `run [--camera N] [--debug]` | Foreground: camera + gestures + preview window |
| `gui [--camera N]` | Open the desktop control-panel window |
| `tray [--camera N]` | Background: run with a system-tray icon (no console) |
| `set-gesture <two\|grab\|swipe>` | Choose the gesture style (default two-finger) |
| `autostart <on\|off\|status>` | Start the tray app automatically on login |
| `info` | Show this device's name, IP, port, settings and log path |
| `set-sensitivity <d>` | Swipe travel needed to fire, 0.05 (easy) .. 0.9 (hard) |
| `set-auto-open <on\|off>` | Auto-open received images in the default viewer |
| `set-animation <on\|off>` | Gradient send border around the sent window on each throw |
| `set-animation-style <name>` | Border style: aurora, rainbow, ocean, sunset, mono |
| `preview [style] [--window]` | Simulate the send border (no image sent) to see the styles |
| `set-resend-block <secs>` | Ignore same file→same peer repeats within N secs (0 = off) |
| `camtest` | Probe camera indices and report which deliver frames |
| `selftest` | Load the hand model and process a frame (no camera needed) |
| `logs [-n N]` | Print the tail of the log file |
| `set-name <name>` | Set the name peers see |
| `peers` | List left/right assignments |
| `set-peer <slot> <name> <ip> [port]` | Assign a peer to a direction |
| `clear-peer <slot>` | Unassign a slot |
| `devices` | List all targets; 3+ enables the on-screen picker |
| `add-device <name> <ip> [port]` | Add an extra device (beyond left/right) |
| `remove-device <name>` | Remove an extra device |
| `preview-select` | Simulate the device picker (no send) to see it |
| `ping <slot>` | Check a peer is reachable |

## Tuning the gesture

If swipes fire too easily or not enough, edit the constants at the top of
`airthrow/gestures.py`:

- `SWIPE_MIN_DISTANCE` — how far the hand must travel (fraction of frame width)
- `SWIPE_MAX_DURATION` — how fast that travel must be
- `COOLDOWN_SECONDS` — minimum gap between throws

## Debugging

Run with `--debug` for a verbose console and an on-screen diagnostic overlay:

```bash
python -m airthrow run --debug      # or:  airthrow run --debug
```

The overlay adds:
- **FPS** and **Hand YES/no** — is a hand being detected at all?
- a **palm marker** sliding along a horizontal track (your hand's position)
- a **swipe-progress bar** — how close the current motion is to the threshold
  that fires a throw (fills green at 100%), plus the live `dx` and direction

Everything is also written to a log file (full detail, always):

```
~/.airthrow/airthrow.log
```

View it any time with `airthrow logs` (e.g. `airthrow logs -n 100`). Logged
events include every swipe (`SWIPE right (dx=+0.41 over 280ms)`), near-misses,
sends, receives, and errors. When something misbehaves, that file is the first
thing to check — and the easiest thing to send over for help.

## Camera preview won't show / no window

If the console prints `camera N open` but **no preview window appears** (and you
have to Ctrl+C), the camera opened but isn't delivering frames. Run:

```bash
airthrow camtest         # shows which index/backend actually works
airthrow run --camera 1  # then use a working index
```

AirThrow tries DirectShow → MSMF on Windows and V4L2 on Linux, and drops to
receive-only mode if frames never arrive (instead of hanging).

## Duplicate throws

Throwing the **same image to the same peer** again within ~10 seconds is
skipped (the overlay shows "Already sent …"), so a double swipe or a hand
lingering in frame won't spam the receiver. A **different image**, a
**different peer**, or an **edited file** always sends. A failed send is not
counted, so you can immediately retry. Change the window with
`set-resend-block <secs>` (e.g. `set-resend-block 30`, or `0` to disable).

## Notes & limits (stage 1)

- **LAN only, no encryption/auth yet** — don't use on untrusted networks. Open
  TCP port `8720` in the firewall if a throw times out.
- **Foreground image detection is heuristic.** It reads the image file the
  focused window's process has open. Viewers that copy the file into memory and
  release it immediately may not be detected; the window-title filename is used
  as a fallback.

## Roadmap

- Auto-discovery of peers on the LAN (mDNS/Zeroconf) — no typing IPs
- More than two directions / any number of devices
- Any file type, and multi-file throws
- Confirmation + encryption for sending outside the LAN

## License

MIT — see [LICENSE](LICENSE).
