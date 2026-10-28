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

**Windows** → produces `dist\airthrow.exe`
```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

**Linux** → produces `dist/airthrow` (run on a Linux machine)
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

## Use

On every machine that should send or receive:

```bash
python -m airthrow run
```

A mirrored webcam window opens showing your left/right peers and the currently
open image. Then:

1. Open an image in any viewer (Photos, your browser, IrfanView, `eog`, …).
2. Swipe your hand **left** or **right** in front of the camera.
3. The open image flies to that direction's peer and lands in its
   `~/.airthrow/Received` folder.

Press `q` or `Esc` to quit.

## Commands

| Command | What it does |
|---|---|
| `run [--camera N] [--debug]` | Start camera + gestures + receiver (receive-only if no camera) |
| `info` | Show this device's name, IP, port and log path |
| `selftest` | Load the hand model and process a frame (no camera needed) |
| `logs [-n N]` | Print the tail of the log file |
| `set-name <name>` | Set the name peers see |
| `peers` | List left/right assignments |
| `set-peer <slot> <name> <ip> [port]` | Assign a peer to a direction |
| `clear-peer <slot>` | Unassign a slot |
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
