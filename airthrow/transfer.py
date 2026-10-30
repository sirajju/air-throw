"""Transfer layer: a tiny HTTP server for receiving files and a send helper.

Each AirThrow instance runs the receiver in a background thread. Sending a file
is a single multipart POST to a peer's /receive endpoint. LAN-only in stage 1,
so there is deliberately no auth/encryption yet.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile

from .config import RECEIVED_DIR, Config
from .logs import get_logger

log = get_logger()

# How a received file is announced. app.py can override these to drive the UI.
# callable(sender: str, filename: str, path: Path, direction: str) | None
on_received = None
# Called as the transfer STARTS (before the body is read), so the receiver can
# show "Receiving <file>..." while a large file is still downloading.
# callable(sender: str, filename: str) | None
on_receiving = None


def build_app(config: Config) -> FastAPI:
    api = FastAPI(title="AirThrow Receiver")

    @api.get("/ping")
    def ping():
        return {"ok": True, "device": config.device_name, "version": "0.1.0"}

    @api.post("/receive")
    async def receive(file: UploadFile = File(...), sender: str = Form("unknown"),
                      direction: str = Form("")):
        RECEIVED_DIR.mkdir(parents=True, exist_ok=True)
        dest = _unique_path(RECEIVED_DIR / Path(file.filename or "file").name)
        if on_receiving:
            try:
                on_receiving(sender, dest.name)
            except Exception:
                pass
        log.info("receiving %s from %s ...", dest.name, sender)
        data = await file.read()
        dest.write_bytes(data)
        if on_received:
            try:
                on_received(sender, dest.name, dest, direction)
            except Exception:  # UI callback must never crash the receiver
                pass
        log.info("RECEIVED %s (%d bytes) from %s -> %s",
                 dest.name, len(data), sender, dest)
        return {"ok": True, "saved_as": dest.name, "bytes": len(data)}

    return api


def _unique_path(path: Path) -> Path:
    """Avoid clobbering an existing file by appending (1), (2), ..."""
    if not path.exists():
        return path
    stem, suffix, i = path.stem, path.suffix, 1
    while True:
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def start_receiver(config: Config) -> threading.Thread:
    """Launch the receiver in a daemon thread and return it."""
    app = build_app(config)
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=config.listen_port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, name="airthrow-receiver", daemon=True)
    thread.start()
    return thread


def send_file(peer_url: str, file_path: Path, sender_name: str,
              direction: str = "", timeout: float = 30.0) -> dict:
    """POST a file to a peer. Raises requests exceptions on failure."""
    file_path = Path(file_path)
    with file_path.open("rb") as fh:
        resp = requests.post(
            f"{peer_url}/receive",
            files={"file": (file_path.name, fh)},
            data={"sender": sender_name, "direction": direction},
            timeout=timeout,
        )
    resp.raise_for_status()
    return resp.json()


def check_peer(peer_url: str, timeout: float = 3.0) -> bool:
    """True if a peer's receiver answers /ping."""
    try:
        resp = requests.get(f"{peer_url}/ping", timeout=timeout)
        return resp.ok
    except requests.RequestException:
        return False
