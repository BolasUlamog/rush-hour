#!/usr/bin/env python3
"""Turn whatever a phone produced into a PIL image the scanner can read.

iPhones shoot HEIC by default and browsers disagree about whether they can
decode it, so decoding happens here rather than in the page: the browser may
hand over a re-encoded JPEG or the original file bytes, and either works.

pillow-heif is used rather than macOS's `sips` so this keeps working when the app
is deployed on Linux.
"""

from __future__ import annotations

import base64
import re
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

MAX_EDGE = 3200          # a sheet photo needs detail, but not 48 megapixels
DATA_URL = re.compile(r"^data:(?P<type>[\w.+-]+/[\w.+-]+)?;base64,(?P<payload>.+)$", re.DOTALL)
ALLOWED = {
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence",
    "image/tiff", "image/bmp",
    # Safari sometimes reports no type at all for a .heic picked from Photos.
    "application/octet-stream", "", None,
}

_registered = False


def register_formats() -> bool:
    """Teach Pillow about HEIC/HEIF. Returns False if the plugin is missing."""
    global _registered
    if _registered:
        return True
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        _registered = True
    except Exception:
        return False
    return True


class ImageError(ValueError):
    """The upload was not an image this app can read."""


def load_bytes(payload: bytes) -> Image.Image:
    """Open image bytes, honour EXIF rotation, and cap the size."""
    register_formats()
    try:
        image = Image.open(BytesIO(payload))
        image.load()
    except Exception as error:
        hint = ""
        if payload[4:12] in (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1", b"ftypmsf1"):
            hint = (" This looks like an iPhone HEIC photo; install pillow-heif "
                    "(pip install -r requirements.txt) to read it.")
        raise ImageError(f"That file could not be opened as an image.{hint}") from error

    # Phone photos carry their rotation in EXIF rather than in the pixels.
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    longest = max(image.size)
    if longest > MAX_EDGE:
        scale = MAX_EDGE / longest
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    return image


def load_data_url(value: str) -> Image.Image:
    """Open a browser `data:` URL, whether it holds a JPEG or a raw HEIC."""
    match = DATA_URL.match(value or "")
    if not match:
        raise ImageError("No photo was uploaded.")
    kind = (match.group("type") or "").lower()
    if kind not in ALLOWED:
        raise ImageError(f"{kind or 'That file type'} is not a supported photo format.")
    try:
        payload = base64.b64decode(match.group("payload"), validate=True)
    except Exception as error:
        raise ImageError("The uploaded photo was not encoded correctly.") from error
    if not payload:
        raise ImageError("The uploaded photo was empty.")
    return load_bytes(payload)


def load_path(path: Path) -> Image.Image:
    return load_bytes(Path(path).read_bytes())
