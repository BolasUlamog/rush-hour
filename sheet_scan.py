#!/usr/bin/env python3
"""Read a photographed Gridlock Sprint answer sheet into structured rows.

Pipeline:
  1. locate the four printed registration squares in the photo
  2. undo the camera's tilt and perspective onto a canonical page image
  3. read the printed puzzle code to identify which puzzle the page is
  4. measure ink in every answer cell to find the rows the student used
  5. crop the ink out of those cells, compose it into tidy text lines, and hand
     that single image to the recognizer

Step 5 matters: Apple's recognizer reliably reads lines of text but routinely
misses a lone character floating in a wide ruled box, so the cells are packed
together into "B U 1" style lines before recognition.

Nothing here decides whether an answer is right. It reports what appears to be
written, with a confidence and a warning list, and the grading station shows
that to a human before any score is awarded.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image, ImageFilter

import glyph_reader
import sheet_code
import sheet_layout as L

SCAN_DPI = 220
GLYPH_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GLYPH_TRUST = 0.80       # below this a lone reader's pick is shown for review
DECISIVE = 0.90          # a reader this sure, this far ahead, settles a disagreement
DECISIVE_MARGIN = 0.35
CLOSE_CALL = 0.20        # a runner-up this near the winner is named in the warning
LINE_H = 104              # digest line height in pixels
SLOT_GAP = 20          # tight: the recognizer only reads these as a line when they sit close
LINE_GAP = 40
MARGIN = 60
CELL_INSET = 2.6          # points trimmed off each cell to drop the printed rule
# Emptiness is judged on ink measured against the row height, never on a plain
# fraction of the cell: a thin "L" fills 0.6% of a wide single-column cell but
# 3% of a narrow three-column one, and a single threshold on that fraction
# silently drops rows. Measured: blank paper lands near 0.003, the lightest real
# character near 0.033.
INK_LOAD_EMPTY = 0.012
# Ink in a cell measured against the row height, so it means the same on a
# wide single-column sheet and a narrow three-column one. An ordinary character
# sits near 0.06; a written-out word such as RIGHT reaches 0.27; a cell scribbled
# out runs higher still. A heavy cell alone proves nothing, so it only counts as
# struck through when no reader can make a confident move out of the row.
INK_LOAD_HEAVY = 0.30
FLAT_STROKE = 9           # max-filter window: wider than a pen stroke, narrower than a cell
FLAT_BLUR = 24.0
CODE_LINE_H = 70
COPY_GAP = 70            # gap between the two written-out copies of a line

# Apple's recognizer is set to English but still returns Cyrillic and Greek
# look-alikes for handwriting, so shapes are folded onto ASCII before matching.
LATIN_LOOKALIKES = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M", "О": "O",
    "Р": "P", "Т": "T", "Х": "X", "У": "Y", "И": "N", "П": "N", "Л": "L", "Д": "D",
    "Ц": "U", "Я": "R", "Г": "F", "З": "3", "Ч": "4", "Ѕ": "S", "Ј": "J",
    "Α": "A", "Β": "B", "Ε": "E", "Η": "H", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O",
    "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X", "Ι": "I", "Ζ": "Z",
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4", "５": "5",
    "—": "-", "–": "-", "’": "", "'": "", "`": "", "·": "",
})

CAR_CONFUSION = {
    "0": "DQ", "O": "DQ", "Q": "QD", "D": "DQ",
    "1": "L", "I": "L", "|": "L", "/": "L", "\\": "L", "T": "L",
    "4": "AH", "A": "A", "H": "HM", "M": "MN", "N": "NM",
    "6": "G", "G": "G", "9": "GQ", "C": "CG", "E": "EF", "F": "FE",
    "8": "B", "B": "B", "R": "PB", "P": "PR", "K": "KX", "X": "XK",
    "S": "5", "5": "S", "J": "J", "2": "Z", "7": "J", "U": "J",
}
DIRECTION_WORDS = {"LEFT": "L", "RIGHT": "R", "UP": "U", "DOWN": "D"}
# Only shape confusions a direction cell can plausibly contain. A direction box
# never holds a car label, so nothing here maps letters like B or E.
DIRECTION_CONFUSION = {
    "L": "L", "1": "L", "I": "L", "|": "L", "/": "L", "\\": "L", "!": "L",
    "R": "R", "P": "R", "К": "R",
    "U": "U", "N": "U", "W": "U", "Ц": "U", "μ": "U",
    "D": "D", "0": "D", "O": "D", "Q": "D",
    "←": "L", "→": "R", "↑": "U", "↓": "D", "<": "L", ">": "R", "^": "U", "v": "D",
}
# Shapes that genuinely could be two different directions: read, but always flagged.
DIRECTION_AMBIGUOUS = {"V": "UD", "M": "UN", "J": "LU"}
SPACES_CONFUSION = {
    "1": "1", "I": "1", "L": "1", "|": "1", "/": "1", "T": "1", "l": "1",
    "2": "2", "Z": "2", "3": "3", "4": "4", "A": "4", "5": "5", "S": "5",
}


class ScanError(Exception):
    """The photo could not be turned into a page; the grader types it by hand."""


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #

def solve_linear(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    rows = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(rows[r][column]))
        if abs(rows[pivot][column]) < 1e-12:
            raise ScanError("The registration marks are in a straight line; reshoot the page.")
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        rows[column] = [value / divisor for value in rows[column]]
        for other in range(size):
            if other == column:
                continue
            factor = rows[other][column]
            if factor:
                rows[other] = [value - factor * rows[column][index] for index, value in enumerate(rows[other])]
    return [row[size] for row in rows]


def perspective_coefficients(output_points, input_points) -> list[float]:
    """Coefficients for PIL's PERSPECTIVE transform, which maps output -> input."""
    matrix, vector = [], []
    for (ox, oy), (ix, iy) in zip(output_points, input_points):
        matrix.append([ox, oy, 1, 0, 0, 0, -ox * ix, -oy * ix])
        vector.append(ix)
        matrix.append([0, 0, 0, ox, oy, 1, -ox * iy, -oy * iy])
        vector.append(iy)
    return solve_linear(matrix, vector)


def percentile(histogram: list[int], fraction: float) -> int:
    total = sum(histogram)
    if not total:
        return 128
    target = total * fraction
    running = 0
    for value, count in enumerate(histogram):
        running += count
        if running >= target:
            return value
    return 255


def bright_bounds(gray: Image.Image) -> tuple[int, int, int, int]:
    """Bounding box of the paper, so corner searches start from the page, not the photo."""
    histogram = gray.histogram()
    low = percentile(histogram, 0.10)
    high = percentile(histogram, 0.97)
    if high - low < 25:
        return (0, 0, gray.width, gray.height)
    cutoff = low + (high - low) * 0.62
    mask = gray.point(lambda value: 255 if value >= cutoff else 0)
    box = mask.getbbox()
    if box is None:
        return (0, 0, gray.width, gray.height)
    return box


def components(mask: bytes, width: int, height: int, limit: int = 4000):
    """Dark connected components in a small window, as (area, x0, y0, x1, y1, cx, cy)."""
    seen = bytearray(width * height)
    found = []
    for start in range(width * height):
        if mask[start] == 0 or seen[start]:
            continue
        stack = [start]
        seen[start] = 1
        area = 0
        x0 = x1 = start % width
        y0 = y1 = start // width
        sum_x = sum_y = 0
        while stack:
            index = stack.pop()
            x, y = index % width, index // width
            area += 1
            sum_x += x
            sum_y += y
            x0, x1 = min(x0, x), max(x1, x)
            y0, y1 = min(y0, y), max(y1, y)
            if area > limit:
                break
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < width and 0 <= ny < height:
                    neighbour = ny * width + nx
                    if mask[neighbour] and not seen[neighbour]:
                        seen[neighbour] = 1
                        stack.append(neighbour)
        found.append((area, x0, y0, x1, y1, sum_x / area, sum_y / area))
    return found


def find_marks(image: Image.Image) -> dict[str, tuple[float, float]]:
    """Locate the four registration squares and return their centers in photo pixels."""
    work_width = 1000
    factor = min(1.0, work_width / image.width)
    gray = image.convert("L")
    if factor < 1.0:
        gray = gray.resize((round(image.width * factor), round(image.height * factor)), Image.LANCZOS)
    page = bright_bounds(gray)
    page_w = page[2] - page[0]
    page_h = page[3] - page[1]
    expected = L.MARK_SIZE * page_w / L.PAGE_W
    if expected < 4:
        raise ScanError("The page is too small in this photo; move the camera closer.")

    window_w = max(int(page_w * 0.26), int(expected * 7))
    window_h = max(int(page_h * 0.20), int(expected * 7))
    targets = {
        "tl": (page[0], page[1], 0.0, 0.0),
        "tr": (page[2] - window_w, page[1], 1.0, 0.0),
        "bl": (page[0], page[3] - window_h, 0.0, 1.0),
        "br": (page[2] - window_w, page[3] - window_h, 1.0, 1.0),
    }
    marks: dict[str, tuple[float, float]] = {}
    for corner, (wx, wy, ax, ay) in targets.items():
        box = (max(0, wx), max(0, wy), min(gray.width, wx + window_w), min(gray.height, wy + window_h))
        window = gray.crop(box)
        histogram = window.histogram()
        low, high = percentile(histogram, 0.04), percentile(histogram, 0.96)
        cutoff = low + (high - low) * 0.45 if high - low > 30 else 110
        mask = window.point(lambda value: 1 if value <= cutoff else 0)
        data = mask.tobytes()
        # The mark sits a fixed fraction in from the page corner; prefer blobs there.
        inset = (L.MARK_INSET + L.MARK_SIZE / 2)
        want_x = (inset / L.PAGE_W) * page_w if ax == 0 else window.width - (inset / L.PAGE_W) * page_w
        want_y = (inset / L.PAGE_H) * page_h if ay == 0 else window.height - (inset / L.PAGE_H) * page_h

        best = None
        for area, x0, y0, x1, y1, cx, cy in components(data, window.width, window.height):
            box_w, box_h = x1 - x0 + 1, y1 - y0 + 1
            if not (expected * 0.5 <= box_w <= expected * 2.3):
                continue
            if not (expected * 0.5 <= box_h <= expected * 2.3):
                continue
            aspect = box_w / box_h
            if not 0.6 <= aspect <= 1.65:
                continue
            fill = area / (box_w * box_h)
            if fill < 0.78:
                continue
            distance = ((cx - want_x) ** 2 + (cy - want_y) ** 2) ** 0.5
            score = distance / max(1.0, expected) - fill * 2.0 - min(1.0, area / (expected * expected))
            if best is None or score < best[0]:
                best = (score, cx + box[0], cy + box[1])
        if best is None:
            raise ScanError(
                f"Could not find the {corner.upper()} corner mark. Photograph the whole sheet, "
                "including all four black corner squares, on a flat surface."
            )
        marks[corner] = (best[1] / factor, best[2] / factor)
    return marks


def validate_marks(marks: dict[str, tuple[float, float]]) -> None:
    """Reject a set of marks that cannot be the corners of this page.

    Without this, one mis-detected blob silently warps the page into nonsense and
    the grader is shown confident-looking garbage.
    """
    def distance(a, b):
        return ((marks[a][0] - marks[b][0]) ** 2 + (marks[a][1] - marks[b][1]) ** 2) ** 0.5

    top, bottom = distance("tl", "tr"), distance("bl", "br")
    left, right = distance("tl", "bl"), distance("tr", "br")
    if min(top, bottom, left, right) < 40:
        raise ScanError("The corner marks are too close together to be this sheet. Reshoot the whole page.")
    if abs(top - bottom) > 0.22 * max(top, bottom) or abs(left - right) > 0.22 * max(left, right):
        raise ScanError(
            "The four corner marks do not line up as a rectangle. Lay the sheet flat, "
            "photograph it straight on, and keep all four black squares in frame."
        )
    span = L.PAGE_W - 2 * (L.MARK_INSET + L.MARK_SIZE / 2)
    rise = L.PAGE_H - 2 * (L.MARK_INSET + L.MARK_SIZE / 2)
    expected = span / rise
    measured = ((top + bottom) / 2) / max(1.0, (left + right) / 2)
    portrait_ok = abs(measured - expected) <= expected * 0.16
    landscape_ok = abs(measured - 1 / expected) <= (1 / expected) * 0.16
    if not (portrait_ok or landscape_ok):
        raise ScanError(
            "The corner marks are the wrong shape for this sheet, so something else was mistaken "
            "for a mark. Reshoot the whole page against a plain background."
        )


def rectify(image: Image.Image, marks: dict, rotation: int, dpi: int = SCAN_DPI) -> Image.Image:
    """Warp the photo onto a flat page image, trying one of four corner assignments."""
    scale = dpi / 72.0
    out_w, out_h = round(L.PAGE_W * scale), round(L.PAGE_H * scale)
    canonical = L.mark_centers()
    output_points = [
        (canonical["tl"][0] * scale, (L.PAGE_H - canonical["tl"][1]) * scale),
        (canonical["tr"][0] * scale, (L.PAGE_H - canonical["tr"][1]) * scale),
        (canonical["br"][0] * scale, (L.PAGE_H - canonical["br"][1]) * scale),
        (canonical["bl"][0] * scale, (L.PAGE_H - canonical["bl"][1]) * scale),
    ]
    order = ["tl", "tr", "br", "bl"]
    shifted = order[rotation:] + order[:rotation]
    input_points = [marks[corner] for corner in shifted]
    coefficients = perspective_coefficients(output_points, input_points)
    return image.transform((out_w, out_h), Image.PERSPECTIVE, coefficients, Image.BICUBIC, fillcolor=(255, 255, 255))


# --------------------------------------------------------------------------- #
# cells
# --------------------------------------------------------------------------- #

def flatten_illumination(gray: Image.Image) -> Image.Image:
    """Even out the lighting so one ink threshold means the same thing everywhere.

    A phone photo of a sheet is rarely evenly lit: a shadow across part of the
    page drags blank paper down to the brightness of ink elsewhere, and a single
    global threshold then reads shadowed blank cells as solid ink. Estimating the
    paper brightness locally and dividing it out fixes that at the source, and it
    also hands both readers a properly contrasted crop.

    The paper level is estimated with a maximum filter, which erases pen strokes
    while keeping the shading, followed by a blur to smooth what is left.
    """
    background = gray.filter(ImageFilter.MaxFilter(FLAT_STROKE)).filter(ImageFilter.GaussianBlur(FLAT_BLUR))
    pixels = np.asarray(gray, dtype=np.float32)
    paper = np.asarray(background, dtype=np.float32)
    flattened = pixels / np.maximum(paper, 1.0) * 255.0
    return Image.fromarray(np.clip(flattened, 0, 255).astype(np.uint8), mode="L")


class CellInk(NamedTuple):
    """What a single printed cell contains."""

    ratio: float          # dark pixels as a fraction of the cell
    crop: Image.Image | None
    load: float           # dark pixels against the row height, layout independent


class Sheet:
    """A rectified page, with helpers to read its printed rectangles."""

    def __init__(self, image: Image.Image, dpi: int = SCAN_DPI):
        self.image = image
        self.raw = image.convert("L")
        self.gray = flatten_illumination(self.raw)
        self.scale = dpi / 72.0
        # On the flattened page paper sits near 255, so the threshold is fixed
        # rather than derived from a global histogram that a shadow would skew.
        self.ink_cutoff = 205

    def box(self, rect, inset: float = 0.0) -> tuple[int, int, int, int]:
        x, y, w, h = rect
        left = (x + inset) * self.scale
        right = (x + w - inset) * self.scale
        top = (L.PAGE_H - (y + h - inset)) * self.scale
        bottom = (L.PAGE_H - (y + inset)) * self.scale
        return (round(left), round(top), round(right), round(bottom))

    def ink(self, rect, inset: float = CELL_INSET) -> CellInk:
        """Measure one printed cell and crop whatever was written in it."""
        crop = self.gray.crop(self.box(rect, inset))
        if crop.width < 4 or crop.height < 4:
            return CellInk(0.0, None, 0.0)
        mask = crop.point(lambda value: 255 if value <= self.ink_cutoff else 0)
        mask = mask.filter(ImageFilter.MedianFilter(3))
        dark = sum(mask.histogram()[128:])
        ratio = dark / max(1, crop.width * crop.height)
        load = dark / max(1.0, (rect[3] * self.scale) ** 2)
        if load < INK_LOAD_EMPTY:
            return CellInk(ratio, None, load)
        bounds = mask.getbbox()
        if bounds is None:
            return CellInk(ratio, None, load)
        # A stray speck is not an answer; a real character fills much of the row.
        if (bounds[3] - bounds[1]) < crop.height * 0.22:
            return CellInk(ratio, None, load)
        pad = max(2, round(1.5 * self.scale))
        bounds = (
            max(0, bounds[0] - pad), max(0, bounds[1] - pad),
            min(crop.width, bounds[2] + pad), min(crop.height, bounds[3] + pad),
        )
        return CellInk(ratio, crop.crop(bounds), load)


def compose_digest(sheet: Sheet, page: dict, used_rows: list[tuple[int, dict]], copies: int = 1):
    """Pack the cropped ink into tidy text lines the recognizer can actually read.

    Returns the digest image and a map from pixel position back to
    (line kind, row index, column name).
    """
    lines = []

    team_slots = []
    for index, rect in enumerate(page["team"]):
        crop = sheet.ink(rect, inset=3.4).crop
        if crop is not None:
            team_slots.append((f"team{index}", scale_to_height(crop, LINE_H)))
    if team_slots:
        lines.append({"kind": "team", "row": -1, "slots": team_slots, "copies": copies})

    for row_index, cells in used_rows:
        slots = []
        for name in ("car", "direction", "spaces"):
            crop = cells.get(name)
            if crop is not None:
                slots.append((name, scale_to_height(crop, LINE_H)))
        if slots:
            lines.append({"kind": "row", "row": row_index, "slots": slots, "copies": copies})

    width = MARGIN * 2 + max(line_width(line) for line in lines)
    height = MARGIN * 2 + sum(max(image.height for _, image in line["slots"]) for line in lines) \
        + LINE_GAP * (len(lines) - 1)
    digest = Image.new("L", (width, height), 255)

    slot_map = []
    y = MARGIN
    for line in lines:
        line_h = max(image.height for _, image in line["slots"])
        x = MARGIN
        for copy in range(line.get("copies", 1)):
            if copy:
                x += COPY_GAP - SLOT_GAP
            for name, image in line["slots"]:
                digest.paste(image, (x, y + (line_h - image.height) // 2))
                slot_map.append({
                    "kind": line["kind"], "row": line["row"], "column": name, "copy": copy,
                    "x0": x, "x1": x + image.width, "y0": y, "y1": y + line_h,
                })
                x += image.width + SLOT_GAP
        y += line_h + LINE_GAP
    return digest, slot_map


def line_width(line: dict) -> int:
    slots = line["slots"]
    one = sum(image.width for _, image in slots) + SLOT_GAP * (len(slots) - 1)
    copies = line.get("copies", 1)
    return one * copies + COPY_GAP * (copies - 1)


def merge_readings(first: dict, second: dict) -> dict:
    """Pool two recognizer passes; each reading keeps its best confidence."""
    merged = {"slots": {}, "lines": {}}
    for part in ("slots", "lines"):
        for source in (first[part], second[part]):
            for key, entries in source.items():
                bucket = merged[part].setdefault(key, {})
                for entry in entries:
                    bucket[entry["text"]] = max(bucket.get(entry["text"], 0.0), entry["confidence"])
        merged[part] = {
            key: [{"text": text, "confidence": confidence}
                  for text, confidence in sorted(texts.items(), key=lambda item: -item[1])]
            for key, texts in merged[part].items()
        }
    return merged


def scale_to_height(image: Image.Image, height: int) -> Image.Image:
    if image.height == height:
        return image
    width = max(6, round(image.width * height / max(1, image.height)))
    return image.resize((width, height), Image.LANCZOS)


# --------------------------------------------------------------------------- #
# recognition
# --------------------------------------------------------------------------- #

def group_by_box(glyphs: list[dict]) -> list[list[dict]]:
    """Split a reading into the words the recognizer boxed, keeping string order."""
    groups: list[list[dict]] = []
    for glyph in glyphs:
        if glyph["c"].isspace():
            continue
        box = (round(glyph["x"], 4), round(glyph["w"], 4))
        if groups and groups[-1][0]["box"] == box:
            groups[-1].append({**glyph, "box": box})
        else:
            groups.append([{**glyph, "box": box}])
    return groups


def read_digest(binary: Path, digest: Image.Image, slot_map: list[dict]) -> dict:
    """Recognize the digest and hand every word back to the slot it came from.

    Returns per-slot readings plus whole-line readings. Both are needed: the
    recognizer sometimes boxes "B U 1" as three words and sometimes as one, and a
    single box spanning three columns has to be split back out by position.
    """
    with tempfile.TemporaryDirectory() as folder:
        image_path = Path(folder) / "digest.png"
        digest.save(image_path)
        regions_path = Path(folder) / "regions.json"
        regions_path.write_text(json.dumps([
            {"id": "digest", "rect": [0, 0, digest.width, digest.height]}
        ]))
        process = subprocess.run(
            [str(binary), str(image_path), "--regions", str(regions_path)],
            capture_output=True, text=True, timeout=90,
        )
    if process.returncode != 0:
        raise ScanError(process.stderr.strip() or "On-device recognition failed.")
    results = json.loads(process.stdout)
    candidates = results[0]["candidates"] if results else []

    slots: dict[tuple, dict[str, float]] = {}
    lines: dict[tuple, dict[str, float]] = {}

    def remember(store, key, text, confidence):
        text = text.strip()
        if not text:
            return
        bucket = store.setdefault(key, {})
        bucket[text] = max(bucket.get(text, 0.0), confidence)

    for candidate in candidates:
        for group in group_by_box(candidate.get("glyphs", [])):
            left = min(glyph["x"] for glyph in group) * digest.width
            right = max(glyph["x"] + glyph["w"] for glyph in group) * digest.width
            centre_y = sum(glyph["y"] + glyph["h"] / 2 for glyph in group) / len(group) * digest.height
            line = [
                slot for slot in slot_map
                if slot["y0"] - LINE_GAP * 0.4 <= centre_y <= slot["y1"] + LINE_GAP * 0.4
            ]
            if not line:
                continue
            spanned = []
            for slot in sorted(line, key=lambda item: item["x0"]):
                overlap = min(right, slot["x1"]) - max(left, slot["x0"])
                if overlap > 0.3 * min(slot["x1"] - slot["x0"], max(1.0, right - left)):
                    spanned.append(slot)
            if not spanned:
                continue
            characters = [glyph["c"] for glyph in group]
            text = "".join(characters)
            head = spanned[0]
            if len(spanned) == 1:
                remember(slots, (head["kind"], head["row"], head["column"]), text, candidate["confidence"])
            elif len(spanned) == len(characters):
                # One box covering several columns, one character each: split it.
                for slot, character in zip(spanned, characters):
                    remember(slots, (slot["kind"], slot["row"], slot["column"]), character,
                             candidate["confidence"] * 0.9)
            else:
                remember(lines, (head["kind"], head["row"]), text, candidate["confidence"])

    def ranked(store):
        return {
            key: [{"text": text, "confidence": confidence}
                  for text, confidence in sorted(texts.items(), key=lambda item: -item[1])]
            for key, texts in store.items()
        }

    return {"slots": ranked(slots), "lines": ranked(lines)}


def read_glyphs(reader: glyph_reader.GlyphReader, page: dict, cells: dict, car_ids: list[str]) -> dict:
    """Classify every written cell with the character model.

    Each column is read against only the characters it can hold, which is what
    lifts this well above a general text recognizer on isolated handwriting.
    """
    car_alphabet = "".join(dict.fromkeys(car_ids))
    columns = {
        "car": (car_alphabet, ""),
        "direction": ("LRUD", GLYPH_ALPHABET),
        "spaces": ("12345", "0123456789"),
    }
    readings: dict[tuple, list[dict]] = {}
    for key, crop in cells.items():
        kind, _row, column = key
        if kind == "team":
            ranked = reader.read_cell(crop, GLYPH_ALPHABET, GLYPH_ALPHABET)
        else:
            single, multi = columns.get(column, (GLYPH_ALPHABET, ""))
            ranked = reader.read_cell(crop, single, multi)
        if ranked:
            readings[key] = [{"text": text, "confidence": score, "source": "glyphs"}
                             for text, score in ranked]
    return readings


def best_exact(readings: list[dict], source: str | None = None) -> str | None:
    """The top reading from one source, ignoring shape-confusion repairs."""
    for reading in readings:
        if source and reading.get("source") != source:
            continue
        text = clean(reading["text"])
        if text:
            return text
    return None


def clean(text: str) -> str:
    folded = text.translate(LATIN_LOOKALIKES).upper()
    return "".join(character for character in folded if not character.isspace())


def _best(scored: list[tuple[float, str, float, bool]]) -> tuple[str | None, float, bool]:
    """Pick the highest scoring reading; exact matches outrank repaired ones."""
    if not scored:
        return None, 0.0, False
    score, value, confidence, corrected = max(scored, key=lambda item: item[0])
    return value, confidence, corrected


def pick_car(readings: list[dict], car_ids: list[str]) -> tuple[str | None, float, bool]:
    """Resolve a car cell against the labels this puzzle actually uses."""
    scored = []
    for reading in readings:
        text = clean(reading["text"])
        confidence = reading["confidence"]
        if not text:
            continue
        if text in car_ids:
            scored.append((confidence + 1.0, text, confidence, False))
            continue
        head = text[0]
        if head in car_ids:
            scored.append((confidence + 0.5, head, confidence, len(text) > 1))
            continue
        for option in CAR_CONFUSION.get(head, ""):
            if option in car_ids:
                scored.append((confidence + 0.2, option, confidence, True))
                break
    return _best(scored)


def pick_direction(readings: list[dict]) -> tuple[str | None, float, bool]:
    scored = []
    for reading in readings:
        text = clean(reading["text"])
        confidence = reading["confidence"]
        if not text:
            continue
        if text in DIRECTION_WORDS:
            scored.append((confidence + 1.0, DIRECTION_WORDS[text], confidence, False))
            continue
        if len(text) == 1 and text in "LRUD":
            scored.append((confidence + 1.0, text, confidence, False))
            continue
        word = next((letter for name, letter in DIRECTION_WORDS.items()
                     if len(text) >= 3 and name.startswith(text[:3])), None)
        if word:
            scored.append((confidence + 0.6, word, confidence, True))
            continue
        head = text[0]
        if head in DIRECTION_CONFUSION:
            scored.append((confidence + 0.25, DIRECTION_CONFUSION[head], confidence, head not in "LRUD"))
            continue
        if head in DIRECTION_AMBIGUOUS:
            scored.append((confidence * 0.5, DIRECTION_AMBIGUOUS[head][0], confidence * 0.5, True))
    return _best(scored)


def pick_spaces(readings: list[dict]) -> tuple[str | None, float, bool]:
    scored = []
    for reading in readings:
        text = clean(reading["text"])
        confidence = reading["confidence"]
        if not text:
            continue
        head = text[0]
        if head in "12345":
            scored.append((confidence + 1.0, head, confidence, len(text) > 1))
            continue
        if head.isdigit():
            continue          # a car cannot slide more than five squares on this board
        if head in SPACES_CONFUSION:
            scored.append((confidence + 0.25, SPACES_CONFUSION[head], confidence, True))
    return _best(scored)


class Cell:
    """What the two readers made of one cell, and whether a human should look."""

    __slots__ = ("value", "confidence", "review", "reason", "alternative", "disputed")

    def __init__(self, value=None, confidence=0.0, review=False, reason="", alternative=None,
                 disputed=False):
        self.value = value
        self.confidence = confidence
        self.review = review
        self.reason = reason
        self.alternative = alternative      # the next best reading, if there was one
        self.disputed = disputed            # the two readers read this cell differently


def two_best(entries: list[dict], picker, *picker_args):
    """The best reading and the best *different* reading from the same entries.

    A grader checking a flagged row wants to know the actual choice in front of
    them, so a near-tie reports what it was nearly tied with.
    """
    value, confidence, corrected = picker(entries, *picker_args)
    if value is None:
        return (None, 0.0, False), (None, 0.0)
    remaining = []
    for entry in entries:
        candidate, _, _ = picker([entry], *picker_args)
        if candidate is not None and candidate != value:
            remaining.append(entry)
    other, other_confidence, _ = picker(remaining, *picker_args) if remaining else (None, 0.0, False)
    return (value, confidence, corrected), (other, other_confidence)


def resolve_cell(entries: list[dict], picker, *picker_args) -> Cell:
    """Combine the character model and the text recognizer for one cell.

    Agreement between two independent readers is the strongest signal available
    here, so agreement passes quietly and disagreement always asks for a human.
    A lone reader is trusted only when it is confident.
    """
    model_entries = [entry for entry in entries if entry.get("source") == "glyphs"]
    text_entries = [entry for entry in entries if entry.get("source") != "glyphs"]
    (model_value, model_confidence, model_fixed), model_runner_up = two_best(model_entries, picker, *picker_args)
    (text_value, text_confidence, text_fixed), _ = two_best(text_entries, picker, *picker_args)

    if model_value and text_value:
        if model_value == text_value:
            return Cell(model_value, max(model_confidence, text_confidence),
                        model_fixed and text_fixed, "", model_runner_up[0])
        winner, winning, loser, losing = (
            (model_value, model_confidence, text_value, text_confidence)
            if model_confidence >= text_confidence
            else (text_value, text_confidence, model_value, model_confidence)
        )
        # One reader being both sure and far ahead of the other is not a real
        # dispute; flagging those buries the genuine ones the grader must see.
        settled = winning >= DECISIVE and winning - losing >= DECISIVE_MARGIN
        return Cell(winner, winning, not settled,
                    "" if settled else f"read as {winner}, not {loser}", loser, disputed=True)
    if model_value:
        weak = model_confidence < GLYPH_TRUST
        other, other_confidence = model_runner_up
        reason = ""
        if weak and other and model_confidence - other_confidence <= CLOSE_CALL:
            reason = f"read as {model_value}, but could be {other}"
        elif weak:
            reason = f"{model_value} is not a clear shape"
        return Cell(model_value, model_confidence, weak or model_fixed, reason, other)
    if text_value:
        weak = text_confidence < 0.5
        return Cell(text_value, text_confidence, weak or text_fixed, "")
    return Cell()


def pick_team_character(readings: list[dict]) -> tuple[str | None, float, bool]:
    """One box of a team ID: any letter or digit, so nothing can be ruled out."""
    scored = []
    for reading in readings:
        text = clean(reading["text"])
        confidence = reading["confidence"]
        if not text:
            continue
        head = text[0]
        if head.isalnum():
            scored.append((confidence + (1.0 if len(text) == 1 else 0.4), head, confidence, len(text) > 1))
    return _best(scored)


def slides(cars: list[dict], car_id: str) -> str:
    """Which directions the named car could ever slide: a car cannot turn."""
    for car in cars:
        if car["id"] == car_id:
            return "LR" if car["orientation"] == "H" else "UD"
    return ""


def settle_against_board(cars: list[dict], car_cell: Cell, direction_cell: Cell) -> str:
    """Break a reader disagreement using what the board physically allows.

    A car slides along one axis only, so a vertical car can never move left. When
    the two readers disagree and exactly one of the readings they offered is
    physically possible, that is the better guess than picking by confidence.

    This only ever chooses between readings that came off the paper, it is only
    applied when the readers already disagreed, and the row stays flagged — so it
    cannot quietly turn a student's illegal move into a legal one.
    """
    if not (car_cell.value and direction_cell.value):
        return ""
    if direction_cell.value in slides(cars, car_cell.value):
        return ""
    options = []
    if car_cell.alternative and direction_cell.value in slides(cars, car_cell.alternative):
        options.append(("car", car_cell.alternative, car_cell.confidence, car_cell))
    if direction_cell.alternative and direction_cell.alternative in slides(cars, car_cell.value):
        options.append(("direction", direction_cell.alternative, direction_cell.confidence, direction_cell))
    if not options:
        return ""
    # Both cells may offer a reading that would make the move possible. Change
    # whichever the readers were least sure of, and if they were equally unsure
    # leave it alone: that is a genuine ambiguity for the grader to settle.
    # A cell the two readers disagreed about is far likelier to be the mistake
    # than one they agreed on, whatever the confidence numbers say. Only when
    # that does not separate them does confidence decide, and only by a clear
    # margin; otherwise this is a real ambiguity and the grader settles it.
    options.sort(key=lambda option: (not option[3].disputed, option[2]))
    if options[0][3].disputed == options[-1][3].disputed and len(options) > 1 \
            and options[1][2] - options[0][2] < CLOSE_CALL:
        return ""
    field, value, _, cell = options[0]
    was, cell.value = cell.value, value
    if field == "car":
        return f"read as {value}, not {was}: {was} cannot move {direction_cell.value}"
    return f"read as {value}, not {was}: {car_cell.value} cannot move {was}"


def parse_line_move(text: str, car_ids: list[str]) -> tuple[str, str, str] | None:
    """Read a whole row that the recognizer returned as one run, e.g. "BU1"."""
    compact = clean(text)
    if len(compact) < 2:
        return None
    car, _, car_corrected = pick_car([{"text": compact[0], "confidence": 1.0}], car_ids)
    direction, _, _ = pick_direction([{"text": compact[1], "confidence": 1.0}])
    spaces, _, _ = pick_spaces([{"text": compact[2], "confidence": 1.0}]) if len(compact) > 2 else (None, 0, False)
    if car and direction and spaces:
        return car, direction, spaces
    return None


def read_sheet_code(binary: Path, page_image: Image.Image) -> tuple[dict, dict] | None:
    """Decode the QR square, returning (puzzle, where it sits on the page).

    A QR decodes whichever way up it is photographed, so its position is what
    tells us whether the page itself is upside down.
    """
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "page.png"
        page_image.save(path)
        process = subprocess.run(
            [str(binary), str(path), "--barcodes"],
            capture_output=True, text=True, timeout=60,
        )
    if process.returncode != 0:
        raise ScanError(process.stderr.strip() or "Could not look for the sheet's QR code.")
    for found in json.loads(process.stdout or "[]"):
        try:
            puzzle = sheet_code.decode(found.get("payload", ""))
        except sheet_code.SheetCodeError:
            continue
        return puzzle, found
    return None


def ensure_recognizer(source: Path, binary: Path) -> Path:
    """Compile the on-device recognizer once, next to the app's temp files."""
    if binary.exists() and binary.stat().st_mtime >= source.stat().st_mtime:
        return binary
    import os

    environment = os.environ.copy()
    cache = Path(tempfile.gettempdir())
    environment["SWIFT_MODULECACHE_PATH"] = str(cache / "gridlock-swift-cache")
    environment["CLANG_MODULE_CACHE_PATH"] = str(cache / "gridlock-clang-cache")
    process = subprocess.run(
        ["/usr/bin/swiftc", str(source), "-o", str(binary)],
        capture_output=True, text=True, timeout=120, env=environment,
    )
    if process.returncode != 0:
        raise ScanError(process.stderr.strip() or "The on-device recognizer could not be prepared.")
    return binary


def scan_sheet(image: Image.Image, binary: Path, dpi: int = SCAN_DPI,
               reader: glyph_reader.GlyphReader | None = None) -> dict:
    """Read one photographed answer sheet using only what is printed on it."""
    image = image.convert("RGB")
    if reader is None:
        reader = glyph_reader.GlyphReader()
    marks = find_marks(image)
    validate_marks(marks)

    quad = [marks["tl"], marks["tr"], marks["br"], marks["bl"]]
    top = ((quad[1][0] - quad[0][0]) ** 2 + (quad[1][1] - quad[0][1]) ** 2) ** 0.5
    side = ((quad[3][0] - quad[0][0]) ** 2 + (quad[3][1] - quad[0][1]) ** 2) ** 0.5
    # A portrait quadrilateral rules out the two sideways corner assignments, so
    # try the likely pair first; the QR's position settles the rest.
    order = [0, 2, 1, 3] if side >= top else [1, 3, 0, 2]

    decoded = None
    for rotation in order:
        page_image = rectify(image, marks, rotation, dpi)
        found = read_sheet_code(binary, page_image)
        if found is None:
            continue
        puzzle, placement = found
        centre_x = placement["x"] + placement["w"] / 2
        centre_y = placement["y"] + placement["h"] / 2
        if centre_x > 0.5 and centre_y < 0.5:      # printed top-right
            decoded = (puzzle, page_image, rotation)
            break
        decoded = decoded or (puzzle, page_image, rotation)
    if decoded is None:
        raise ScanError(
            "Could not read the QR square on this sheet. Photograph the whole page in even "
            "light, keeping the square in the top corner clean and unfolded."
        )

    puzzle, page_image, rotation = decoded
    result = read_page(page_image, puzzle, binary, dpi, reader)
    result["rotation"] = rotation
    return result


def read_page(page_image: Image.Image, puzzle: dict, binary: Path, dpi: int,
              reader: glyph_reader.GlyphReader) -> dict:
    """Read the team ID and the answer rows off an already flattened page."""
    sheet = Sheet(page_image, dpi)
    # The sheet's own QR states how many answer rows it was printed with, so the
    # geometry is exact even if this file's layout has changed since it printed.
    page = L.layout(puzzle["rows"])
    car_ids = [car["id"] for car in puzzle["cars"]]

    used_rows, ink_rows = [], []
    for index, cells in enumerate(page["answerRows"]):
        crops, measured = {}, {}
        for name in ("car", "direction", "spaces"):
            reading = sheet.ink(cells[name])
            measured[name] = reading
            if reading.crop is not None:
                crops[name] = reading.crop
        if crops:
            used_rows.append((index, crops))
            ink_rows.append((index, measured))

    # The character model reads the cells. Apple's text recognizer is kept as an
    # independent second opinion, and for directions written out as words.
    cell_crops: dict[tuple, Image.Image] = {}
    for row_index, row_crops in used_rows:
        for column, crop in row_crops.items():
            cell_crops[("row", row_index, column)] = crop
    for box_index, rect in enumerate(page["team"]):
        crop = sheet.ink(rect, inset=3.4).crop
        if crop is not None:
            cell_crops[("team", -1, f"team{box_index}")] = crop
    glyphs = read_glyphs(reader, page, cell_crops, car_ids) if reader.available else {}

    slots: dict[tuple, list[dict]] = {}
    lines: dict[tuple, list[dict]] = {}
    if used_rows or cell_crops:
        # Two passes: a compact line reads a full row best, while the same line
        # written out twice is what makes the recognizer notice a lone character.
        single = compose_digest(sheet, page, used_rows, copies=1)
        doubled = compose_digest(sheet, page, used_rows, copies=2)
        readings = merge_readings(
            read_digest(binary, single[0], single[1]),
            read_digest(binary, doubled[0], doubled[1]),
        )
        lines = readings["lines"]
        slots = {
            key: [*glyphs.get(key, []), *readings["slots"].get(key, [])]
            for key in set(glyphs) | set(readings["slots"])
        }

    team_chars, team_confidences = [], []
    team_uncertain = False
    for index in range(len(page["team"])):
        entries = slots.get(("team", -1, f"team{index}"), [])
        if not entries:
            continue
        # A team ID has no restricted alphabet, so 1/I and 0/O are genuinely
        # ambiguous. Both readers must agree or the grader is asked to confirm.
        cell = resolve_cell(entries, pick_team_character)
        if not cell.value:
            continue
        team_chars.append(cell.value)
        team_confidences.append(cell.confidence)
        if cell.review:
            team_uncertain = True
    if not team_chars:
        for entry in lines.get(("team", -1), []):
            text = clean(entry["text"])
            if text:
                team_chars = list(text)
                team_confidences = [entry["confidence"]]
                team_uncertain = True
                break

    rows, warnings = [], []
    for index, measured in ink_rows:
        car_cell = resolve_cell(slots.get(("row", index, "car"), []), pick_car, car_ids)
        direction_cell = resolve_cell(slots.get(("row", index, "direction"), []), pick_direction)
        spaces_cell = resolve_cell(slots.get(("row", index, "spaces"), []), pick_spaces)
        impossible = settle_against_board(puzzle["cars"], car_cell, direction_cell)
        if impossible:
            car_cell.review = direction_cell.review = True
            car_cell.reason = impossible
            direction_cell.reason = ""
        car, direction, spaces = car_cell.value, direction_cell.value, spaces_cell.value
        confidences = [cell.confidence for cell in (car_cell, direction_cell, spaces_cell) if cell.value]
        queries = [f"{name} {cell.reason}" for name, cell in
                   (("car", car_cell), ("direction", direction_cell), ("spaces", spaces_cell))
                   if cell.review and cell.reason]
        corrected = any(cell.review for cell in (car_cell, direction_cell, spaces_cell))

        if not (car and direction and spaces):
            for entry in lines.get(("row", index), []):
                parsed = parse_line_move(entry["text"], car_ids)
                if parsed:
                    car, direction, spaces = parsed
                    confidences = [entry["confidence"]]
                    corrected = True
                    break

        move = f"{car}{direction}{spaces}" if car and direction and spaces else None
        confidence = round(sum(confidences) / len(confidences), 3) if confidences and move else 0.0

        # The sheet tells students to cross a mistake out and use the next row.
        # A heavily inked cell that no reader can turn into a confident move is
        # taken at its word and dropped, rather than contributing a guess that
        # would fail an otherwise correct route.
        heavy = [name for name, reading in measured.items() if reading.load >= INK_LOAD_HEAVY]
        struck = bool(heavy) and (move is None or corrected)
        if struck:
            move = None

        rows.append({
            "row": index + 1,
            "car": car,
            "direction": direction,
            "spaces": spaces,
            "move": move,
            "struck": struck,
            "confidence": 0.0 if struck else confidence,
            "corrected": corrected,
            "needsReview": struck or move is None or corrected or confidence < 0.5,
            "ink": {name: round(reading.ratio, 4) for name, reading in measured.items()},
            "load": {name: round(reading.load, 3) for name, reading in measured.items()},
        })
        if struck:
            warnings.append(
                f"Row {index + 1} looks crossed out, so it was left out of the route. "
                "Check the paper in case it was meant to count."
            )
        elif move is None:
            missing = [name for name, value in (("car", car), ("direction", direction), ("spaces", spaces)) if not value]
            warnings.append(f"Row {index + 1}: could not read {', '.join(missing)}. Type this row in by hand.")
        elif queries:
            warnings.append(f"Row {index + 1}: read as {move} \u2014 {'; '.join(queries)}. Check the paper.")
        elif corrected or confidence < 0.5:
            warnings.append(f"Row {index + 1}: read as {move} \u2014 check it against the paper.")

    team = "".join(team_chars)
    team_confidence = round(sum(team_confidences) / len(team_confidences), 3) if team_confidences else 0.0
    if not team:
        warnings.insert(0, "The team ID boxes look empty or unreadable. Enter the team ID by hand.")
    elif team_uncertain or team_confidence < 0.5:
        warnings.insert(0, f"Team ID read as \u201c{team}\u201d \u2014 confirm it before saving the score.")

    moves = [row["move"] for row in rows if row["move"]]
    scores = [row["confidence"] for row in rows if row["move"]]
    return {
        "puzzleCode": puzzle["code"],
        "points": puzzle["points"],
        "sheetRows": puzzle["rows"],
        "cars": puzzle["cars"],
        "team": team,
        "teamConfidence": team_confidence,
        "rows": rows,
        "moves": moves,
        "confidence": round(min(scores), 3) if scores else 0.0,
        "warnings": warnings,
        "source": "glyph model + on-device text" if reader.available else "on-device text only",
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Scan a filled answer sheet; the sheet describes itself.")
    parser.add_argument("image", type=Path)
    parser.add_argument("--dpi", type=int, default=SCAN_DPI)
    arguments = parser.parse_args()

    app_dir = Path(__file__).resolve().parent
    binary = ensure_recognizer(app_dir / "handwriting_ocr.swift",
                               Path(tempfile.gettempdir()) / "gridlock-handwriting-ocr")
    import image_input

    image = image_input.load_path(arguments.image)
    print(json.dumps(scan_sheet(image, binary, arguments.dpi), indent=2))


if __name__ == "__main__":
    main()
