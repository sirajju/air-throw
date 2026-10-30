"""Decide WHAT a throw sends — the payload resolution chain.

IP Messenger (and our own HTTP transfer) carry any file type, so a throw is no
longer images-only. The question becomes: what is the user most clearly
pointing at? First match wins:

1. **Clipboard files** — Ctrl+C on file(s) anywhere. The universal override:
   whatever you copy, you throw (IDE, Explorer, viewer — doesn't matter).
2. **Explorer selection** — the item(s) selected in the focused Explorer window.
3. **Foreground document** — the image open in the focused viewer (existing
   detection).
4. **Clipboard image** — a copied screenshot, saved to a temp PNG.
5. **Last seen image** — the old behaviour, as a final fallback.

Multiple files or a folder are bundled into one temp **.zip** so they arrive as
a single item on any transport.
"""

from __future__ import annotations

import tempfile
import time
import zipfile
from pathlib import Path

from .logs import get_logger

log = get_logger()

OUTBOX = Path(tempfile.gettempdir()) / "airthrow-outbox"


# --------------------------------------------------------------------------
# Bundling: 1 file -> itself; folder / many files -> one zip
# --------------------------------------------------------------------------
def _bundle(paths: list[Path]) -> Path | None:
    paths = [Path(p) for p in paths if p and Path(p).exists()]
    if not paths:
        return None
    if len(paths) == 1 and paths[0].is_file():
        return paths[0]

    OUTBOX.mkdir(parents=True, exist_ok=True)
    stem = paths[0].stem or "files"
    name = (f"{stem}.zip" if len(paths) == 1
            else f"{stem}+{len(paths) - 1}more.zip")
    out = OUTBOX / f"{int(time.time())}-{name}"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            if p.is_file():
                zf.write(p, p.name)
            elif p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file():
                        zf.write(f, Path(p.name) / f.relative_to(p))
    log.info("bundled %d item(s) -> %s", len(paths), out.name)
    return out


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------
def _grab_clipboard():
    """PIL's grabclipboard: list of file paths (copied files), a PIL Image
    (copied bitmap/screenshot), or None."""
    try:
        from PIL import ImageGrab
        return ImageGrab.grabclipboard()
    except Exception:
        return None


def _explorer_selection() -> list[Path]:
    """Windows: the selected item(s) in the FOCUSED File Explorer window."""
    try:
        import pythoncom
        import win32com.client
        import win32gui
    except ImportError:
        return []
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        fg = win32gui.GetForegroundWindow()
        shell = win32com.client.Dispatch("Shell.Application")
        for window in shell.Windows():
            try:
                if int(window.HWND) != fg:
                    continue
                items = window.Document.SelectedItems()
                return [Path(items.Item(i).Path) for i in range(items.Count)]
            except Exception:
                continue
    except Exception as exc:
        log.debug("explorer selection failed: %s", exc)
    return []


def _save_clipboard_image(img) -> Path | None:
    try:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        out = OUTBOX / f"clip-{int(time.time())}.png"
        img.save(out, "PNG")
        return out
    except Exception as exc:
        log.debug("could not save clipboard image: %s", exc)
        return None


# --------------------------------------------------------------------------
# The chain
# --------------------------------------------------------------------------
def resolve_payload(last_image: Path | None = None):
    """Return (path, source_description) for what a throw should send,
    or (None, reason) when nothing resolves."""
    clip = _grab_clipboard()

    # 1. Copied file(s) — the universal override.
    if isinstance(clip, list) and clip:
        bundled = _bundle([Path(p) for p in clip])
        if bundled:
            n = len(clip)
            return bundled, ("clipboard file" if n == 1
                             else f"clipboard ({n} items → zip)")

    # 2. Selection in the focused Explorer window.
    sel = _explorer_selection()
    if sel:
        bundled = _bundle(sel)
        if bundled:
            n = len(sel)
            return bundled, ("Explorer selection" if n == 1
                             else f"Explorer selection ({n} → zip)")

    # 3. The image open in the focused viewer (existing detection).
    from .foreground_image import current_image
    doc = current_image()
    if doc:
        return doc, "open image"

    # 4. A copied screenshot/bitmap.
    if clip is not None and not isinstance(clip, list):
        saved = _save_clipboard_image(clip)
        if saved:
            return saved, "clipboard image"

    # 5. Fall back to the last image we saw.
    if last_image and Path(last_image).exists():
        return Path(last_image), "last open image"

    return None, "nothing to send — copy a file or open one"
