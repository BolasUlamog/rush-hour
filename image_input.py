#!/usr/bin/env python3
"""Turn whatever a phone or copier produced into PIL images the scanner can read.

iPhones shoot HEIC by default and browsers disagree about whether they can
decode it, so decoding happens here rather than in the page: the browser may
hand over a re-encoded JPEG or the original file bytes, and either works.

pillow-heif is used rather than macOS's `sips` so this keeps working when the app
is deployed on Linux.

PDFs are read too, and a PDF is the one input that can hold a whole stack: a
volunteer running the pile through a copier gets back one file with thirty sheets
in it. So the unit here is a *list* of pages, and a photograph is simply a
one-page list. Rendering is pypdfium2, which ships a self-contained wheel — no
poppler binary to install, which matters because the deployment cannot run one.
"""

from __future__ import annotations

import base64
import re
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

MAX_EDGE = 3200          # a sheet photo needs detail, but not 48 megapixels
PDF_DPI = 200            # what the corner marks and the QR square need to survive
MAX_PDF_PAGES = 60       # one contest's stack; past this someone fed in the wrong file
DATA_URL = re.compile(r"^data:(?P<type>[\w.+-]+/[\w.+-]+)?;base64,(?P<payload>.+)$", re.DOTALL)
ALLOWED = {
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence",
    "image/tiff", "image/bmp",
    "application/pdf",
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


def is_pdf(payload: bytes) -> bool:
    """PDFs start with %PDF-, sometimes after a few bytes of junk."""
    return payload[:1024].lstrip()[:5] == b"%PDF-"


def load_pdf(payload: bytes, dpi: int = PDF_DPI) -> list[Image.Image]:
    """Render every page of a PDF, in order.

    A scanned page arrives as one big image inside the PDF, and a sheet printed
    straight to PDF arrives as vectors; rendering handles both without having to
    care which, and without needing the page to have been photographed at all.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError as error:
        raise ImageError(
            "This app was deployed without PDF support. Upload a photo of the "
            "sheet instead, or install pypdfium2."
        ) from error

    try:
        document = pdfium.PdfDocument(BytesIO(payload))
        count = len(document)
    except Exception as error:
        raise ImageError("That PDF could not be opened.") from error
    if not count:
        raise ImageError("That PDF has no pages in it.")
    if count > MAX_PDF_PAGES:
        raise ImageError(
            f"That PDF has {count} pages, more than the {MAX_PDF_PAGES} this reads "
            "at once. Split it into smaller batches."
        )

    pages = []
    for number in range(count):
        try:
            rendered = document[number].render(scale=dpi / 72).to_pil()
        except Exception as error:
            raise ImageError(f"Page {number + 1} of that PDF could not be rendered.") from error
        pages.append(fit(rendered))
    return pages


def fit(image: Image.Image) -> Image.Image:
    """Normalize colour mode and cap the long edge."""
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    longest = max(image.size)
    if longest > MAX_EDGE:
        scale = MAX_EDGE / longest
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    return image


def load_all(payload: bytes) -> list[Image.Image]:
    """Every sheet in an upload: a PDF's pages, or the single photo it was."""
    if is_pdf(payload):
        return load_pdf(payload)
    return [load_bytes(payload)]


def load_bytes(payload: bytes) -> Image.Image:
    """Open image bytes, honour EXIF rotation, and cap the size.

    A PDF is accepted here too and gives back its first page, so every existing
    caller that wants one sheet keeps working; use load_all for the whole stack.
    """
    register_formats()
    if is_pdf(payload):
        return load_pdf(payload)[0]
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
    return fit(ImageOps.exif_transpose(image))


def load_data_url(value: str) -> Image.Image:
    """Open a browser `data:` URL, whether it holds a JPEG or a raw HEIC."""
    match = DATA_URL.match(value or "")
    if not match:
        raise ImageError("No photo was uploaded.")
    kind = (match.group("type") or "").lower()
    if kind not in ALLOWED:
        raise ImageError(f"{kind or 'That file type'} is not a supported photo or PDF format.")
    try:
        payload = base64.b64decode(match.group("payload"), validate=True)
    except Exception as error:
        raise ImageError("The uploaded photo was not encoded correctly.") from error
    if not payload:
        raise ImageError("The uploaded photo was empty.")
    return load_bytes(payload)


def load_data_url_pages(value: str) -> list[Image.Image]:
    """Every sheet in a browser `data:` upload, whether photo or PDF."""
    match = DATA_URL.match(value or "")
    if not match:
        raise ImageError("No sheet was uploaded.")
    kind = (match.group("type") or "").lower()
    if kind not in ALLOWED:
        raise ImageError(f"{kind or 'That file type'} is not a supported photo or PDF format.")
    try:
        payload = base64.b64decode(match.group("payload"), validate=True)
    except Exception as error:
        raise ImageError("The upload was not encoded correctly.") from error
    if not payload:
        raise ImageError("The upload was empty.")
    return load_all(payload)


def load_path(path: Path) -> Image.Image:
    return load_bytes(Path(path).read_bytes())


def load_path_pages(path: Path) -> list[Image.Image]:
    return load_all(Path(path).read_bytes())
