#!/usr/bin/env python3
"""Test fixtures: fill a packet page with handwriting-font answers, then fake a photo.

This exists so sheet_scan can be exercised end to end without a printer and a
camera. It is a development tool, not part of the grading station.

    python3 fill_sheet.py output/packets/GS-DEMO/manifest.json --page 1 \
        --team 7B --moves BU1,FD1,XR3 --out tmp/sheets/filled.png --photo
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

import packet_pdf
import sheet_layout as L

PENCIL = HexColor("#1B2733")
HANDWRITING = {
    "bradley": "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
    "comic": "/System/Library/Fonts/Supplemental/Comic Sans MS.ttf",
    "marker": "/System/Library/Fonts/Supplemental/Chalkduster.ttf",
}
DIRECTION_WORDS = {"L": "LEFT", "R": "RIGHT", "U": "UP", "D": "DOWN"}


def register_font(style: str) -> str:
    path = HANDWRITING.get(style, HANDWRITING["bradley"])
    name = f"hand-{style}"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, path))
    return name


def scribble(pdf, text, rect, font, size, rng, jitter=2.0):
    """Draw text roughly centered in a rect with a little human wobble."""
    x, y, w, h = rect
    width = pdfmetrics.stringWidth(text, font, size)
    cx = x + (w - width) / 2 + rng.uniform(-jitter, jitter)
    cy = y + (h - size * 0.7) / 2 + rng.uniform(-jitter, jitter) + size * 0.06
    angle = rng.uniform(-3.5, 3.5)
    pdf.saveState()
    pdf.setFillColor(PENCIL)
    pdf.setFont(font, size)
    pdf.translate(cx + width / 2, cy + size * 0.35)
    pdf.rotate(angle)
    pdf.drawCentredString(0, -size * 0.35, text)
    pdf.restoreState()


def fill_page(manifest: dict, puzzle: dict, team: str, moves: list[str], style: str, seed: int,
              words: bool, out_pdf: Path) -> None:
    font = register_font(style)
    rng = random.Random(seed)
    page = puzzle["layout"]

    def overlay(pdf, _puzzle, page):
        for index, char in enumerate(team.upper()[: len(page["team"])]):
            scribble(pdf, char, page["team"][index], font, page["team"][index][3] * 0.62, rng, jitter=2.5)
        for index, move in enumerate(moves):
            if index >= len(page["answerRows"]):
                break
            row = page["answerRows"][index]
            car, direction, spaces = move[0].upper(), move[1].upper(), move[2:]
            size = row["car"][3] * 0.62
            scribble(pdf, car, row["car"], font, size, rng)
            label = DIRECTION_WORDS[direction] if words else direction
            scribble(pdf, label, row["direction"], font, size, rng)
            scribble(pdf, spaces, row["spaces"], font, size, rng)

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(out_pdf), pagesize=letter, pageCompression=1)
    packet_pdf.draw_puzzle_page(pdf, manifest, puzzle, page, overlay=overlay)
    pdf.save()


def rasterize(pdf_path: Path, png_path: Path, dpi: int = 200) -> None:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(pdf_path))
    image = document[0].render(scale=dpi / 72).to_pil().convert("RGB")
    png_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(png_path)


def fake_photo(png_path: Path, out_path: Path, seed: int = 7, rotate180: bool = False,
               crop_corner: bool = False) -> None:
    """Tilt, perspective-skew, shade and soften the render like a phone snapshot.

    The page is warped into a quadrilateral that always stays inside the frame,
    because a photo missing a corner mark is a different test case (crop_corner).
    """
    from PIL import Image, ImageEnhance, ImageFilter

    rng = random.Random(seed)
    page = Image.open(png_path).convert("RGB")
    if rotate180:
        page = page.rotate(180)
    width, height = page.size

    canvas_w, canvas_h = int(width * 1.18), int(height * 1.14)
    backdrop = Image.new("RGB", (canvas_w, canvas_h), (108, 104, 98))
    ox, oy = (canvas_w - width) // 2, (canvas_h - height) // 2
    backdrop.paste(page, (ox, oy))
    source = [(ox, oy), (ox + width, oy), (ox + width, oy + height), (ox, oy + height)]

    angle = math.radians(rng.uniform(-3.0, 3.0))
    cx, cy = canvas_w / 2, canvas_h / 2
    jitter = width * 0.03
    target = []
    for x, y in source:
        dx, dy = x - cx, y - cy
        rx = cx + dx * math.cos(angle) - dy * math.sin(angle) + rng.uniform(-jitter, jitter)
        ry = cy + dx * math.sin(angle) + dy * math.cos(angle) + rng.uniform(-jitter, jitter)
        target.append((rx, ry))

    # Pull the quad back inside the frame so every corner mark stays visible.
    margin = width * 0.02
    shrink = 1.0
    for x, y in target:
        for value, limit in ((x, canvas_w), (y, canvas_h)):
            centre = limit / 2
            reach = abs(value - centre)
            allowed = centre - margin
            if reach > allowed:
                shrink = min(shrink, allowed / reach)
    target = [(cx + (x - cx) * shrink, cy + (y - cy) * shrink) for x, y in target]

    if crop_corner:
        target = [(x - width * 0.17, y - height * 0.11) for x, y in target]

    coefficients = perspective_coefficients(target, source)
    warped = backdrop.transform((canvas_w, canvas_h), Image.PERSPECTIVE, coefficients, Image.BICUBIC,
                                fillcolor=(108, 104, 98))

    shade = Image.linear_gradient("L").resize((canvas_w, canvas_h)).rotate(rng.uniform(0, 360), fillcolor=128)
    shade = shade.point(lambda value: int(190 + value * 0.26))
    warped = Image.composite(warped, Image.new("RGB", warped.size, (255, 255, 255)), shade)
    warped = ImageEnhance.Brightness(warped).enhance(0.97)
    warped = warped.filter(ImageFilter.GaussianBlur(0.6))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    warped.save(out_path, quality=88)


def perspective_coefficients(source, target):
    """Coefficients for PIL's PERSPECTIVE transform mapping output -> input points."""
    matrix = []
    vector = []
    for (sx, sy), (tx, ty) in zip(source, target):
        matrix.append([sx, sy, 1, 0, 0, 0, -sx * tx, -sy * tx])
        vector.append(tx)
        matrix.append([0, 0, 0, sx, sy, 1, -sx * ty, -sy * ty])
        vector.append(ty)
    return solve(matrix, vector)


def solve(matrix, vector):
    size = len(vector)
    rows = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(rows[r][column]))
        if abs(rows[pivot][column]) < 1e-12:
            raise ValueError("The point correspondences are degenerate.")
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--page", type=int, default=1, help="1-based puzzle page to fill")
    parser.add_argument("--team", default="7B")
    parser.add_argument("--moves", default=None, help="comma separated, e.g. BU1,FD1,XR3 (default: the key)")
    parser.add_argument("--style", default="bradley", choices=sorted(HANDWRITING))
    parser.add_argument("--words", action="store_true", help="write LEFT/RIGHT instead of L/R")
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--photo", action="store_true", help="also emit a tilted, shaded fake photo")
    parser.add_argument("--upside-down", action="store_true", help="photograph the page rotated 180 degrees")
    parser.add_argument("--crop-corner", action="store_true", help="push a corner mark out of frame")
    parser.add_argument("--out", type=Path, default=Path("tmp/sheets/filled.png"))
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())
    if "layout" not in manifest["puzzles"][0]:
        packet_pdf.resolve_pages(manifest)
    puzzle = manifest["puzzles"][args.page - 1]
    moves = [m.strip().upper() for m in args.moves.split(",")] if args.moves else list(puzzle["solution"])

    pdf_path = args.out.with_suffix(".pdf")
    fill_page(manifest, puzzle, args.team, moves, args.style, args.seed, args.words, pdf_path)
    rasterize(pdf_path, args.out, args.dpi)
    print(args.out)
    if args.photo:
        photo = args.out.with_name(args.out.stem + "-photo.jpg")
        fake_photo(args.out, photo, args.seed, args.upside_down, args.crop_corner)
        print(photo)


if __name__ == "__main__":
    main()
