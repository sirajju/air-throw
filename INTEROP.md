# Interop: IP Messenger & LocalSend  (branch: `feature/interop-ipmsg-localsend`)

Goal: send **any file type** and **text messages**, **auto-discover** peers (no
typing IPs), and talk to tools colleagues already run — especially **IP
Messenger**, which is on every office PC. Direction / canvas mapping stays manual.

## Why IP Messenger first

IP Messenger (ipmsg.org) is a LAN tool on UDP+TCP **2425** that already does
**auto-discovery**: every client broadcasts `BR_ENTRY`, so AirThrow can learn the
whole office automatically and send straight to the clients people already have
open. It also transfers **any file type** and **messages** — the full ask.

### What's implemented (`airthrow/ipmsg.py`)

- **Auto-discovery** — broadcast `BR_ENTRY`, answer others with `ANSENTRY`, track
  `BR_EXIT`; a live peer list with no manual IPs.
- **Send a message** — `IPMSG_SENDMSG` (UTF-8), optional delivery check.
- **Send any file** — `IPMSG_SENDMSG | FILEATTACHOPT` announces the file; we run
  the TCP server that streams bytes when the receiver sends `GETFILEDATA`.
- **Receive messages**; `pull_file()` downloads an offered file.

CLI:

```bash
airthrow ipmsg-scan            # auto-discover peers on the LAN
airthrow ipmsg-msg <ip> "hi"   # send a text message
airthrow ipmsg-send <ip> <file> ["message"]   # send any file
```

### The one constraint: port 2425 is single-owner

A host can't run **both** the native IP Messenger **and** AirThrow's ipmsg node —
they'd both need UDP 2425. AirThrow handles this gracefully:

- **Full mode** (AirThrow owns 2425): discovery + messages + file transfer. Use
  this on the *sending* machine by closing the native IP Messenger there; every
  *other* machine keeps IP Messenger and receives normally.
- **Send-only mode** (native IP Messenger still running locally): AirThrow sends
  messages from an ephemeral port (no discovery, no file serving, no delivery
  confirmation). Good enough to fire a message without closing anything.

AirThrow detects which automatically and the CLI tells you.

### Notes

- Messages are sent UTF-8 (`IPMSG_UTF8OPT`); modern IP Messenger decodes it.
- No encryption yet (`IPMSG_ENCRYPTOPT` not implemented) — LAN only.

## LocalSend (planned next on this branch)

[LocalSend](https://github.com/localsend/protocol) adds phones/tablets and any
OS. Protocol v2: multicast discovery on `224.0.0.167:53317`, then HTTP
`POST /api/localsend/v2/prepare-upload` → `POST /api/localsend/v2/upload`. It
would run as a second backend behind the same "discover + throw" UI, so a swipe
could target an IP Messenger PC *or* a LocalSend device. Not yet implemented.

## How this folds into AirThrow

The plan: a transport layer with backends (native AirThrow HTTP · IP Messenger ·
LocalSend). Discovery feeds one peer list; you map discovered peers to
left/right/canvas by hand (as now). A throw then routes over whichever backend
the target speaks — so gestures work against the office's existing IP Messenger
with zero setup on their side.
