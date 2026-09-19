#!/usr/bin/env python3
"""Render a Gridlock Sprint packet: one puzzle page per puzzle, plus an answer key.

Each page carries its own registration marks, a printed puzzle code, write-in
team-ID boxes, the board, and the answer table. Every rectangle comes from
sheet_layout so that sheet_scan crops exactly what this module drew.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import Color, HexColor, black, white
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

import levels
import sheet_code
import sheet_layout as L

INK = HexColor("#18212A")
PAPER = HexColor("#FFFDF7")
GRID = HexColor("#D8D0C0")
RED = HexColor("#E94B35")
YELLOW = HexColor("#F2BD3D")
MUTED = HexColor("#6D706C")
LINE = HexColor("#AAA394")
COLORS = [
    HexColor("#D69C32"), HexColor("#745D8A"), HexColor("#BE6B4A"),
    HexColor("#4D8990"), HexColor("#A96A79"), HexColor("#3F7897"),
    HexColor("#5E917A"), HexColor("#6B7A94"), HexColor("#94744E"),
]


def centered_text(pdf, text, x, y, width, font="Helvetica-Bold", size=11, color=INK):
    pdf.setFont(font, size)
    pdf.setFillColor(color)
    pdf.drawString(x + (width - stringWidth(text, font, size)) / 2, y, text)


def registration_marks(pdf):
    """Four solid squares the scanner uses to undo camera tilt and perspective."""
    pdf.setFillColor(black)
    size = L.MARK_SIZE
    for _, (cx, cy) in L.mark_centers().items():
        pdf.rect(cx - size / 2, cy - size / 2, size, size, fill=1, stroke=0)


def draw_board(pdf, cars, rect, label_size=16, exit_label=True):
    x, y, size, _ = rect
    cell = size / L.GRID
    pdf.setFillColor(GRID)
    pdf.setStrokeColor(INK)
    pdf.setLineWidth(5)
    pdf.rect(x, y, size, size, fill=1, stroke=1)
    pdf.setStrokeColor(Color(1, 1, 1, alpha=0.35))
    pdf.setLineWidth(0.6)
    for i in range(1, L.GRID):
        pdf.line(x + i * cell, y, x + i * cell, y + size)
        pdf.line(x, y + i * cell, x + size, y + i * cell)

    inset = max(2.0, cell * 0.06)
    for index, car in enumerate(cars):
        horizontal = car["orientation"] == "H"
        car_x = x + car["col"] * cell + inset
        car_y = y + size - (car["row"] + (1 if horizontal else car["length"])) * cell + inset
        car_w = (car["length"] if horizontal else 1) * cell - 2 * inset
        car_h = (1 if horizontal else car["length"]) * cell - 2 * inset
        pdf.setFillColor(RED if car["id"] == "X" else COLORS[(index - 1) % len(COLORS)])
        pdf.setStrokeColor(INK)
        pdf.setLineWidth(1.3)
        pdf.roundRect(car_x, car_y, car_w, car_h, min(5, cell * 0.14), fill=1, stroke=1)
        # Capitals only: a handwritten lowercase "l" is unreadable next to "1".
        label = car["id"].upper()
        centered_text(pdf, label, car_x, car_y + car_h / 2 - label_size * 0.33, car_w, size=label_size, color=white)

    exit_y = y + size - 3 * cell
    pdf.setFillColor(PAPER)
    pdf.rect(x + size - 3, exit_y + 3, 9, cell - 6, fill=1, stroke=0)
    if exit_label:
        pdf.setFillColor(RED)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(x + size + 11, exit_y + cell / 2 - 3, "EXIT >")


def draw_sheet_code(pdf, puzzle, page):
    """Print the QR code that makes this sheet gradeable on its own."""
    payload = sheet_code.encode(puzzle["code"], puzzle["points"], page["rows"], puzzle["cars"])
    x, y, width, height = page["qr"]
    widget = qr.QrCodeWidget(payload, barLevel="M", barBorder=0)
    bounds = widget.getBounds()
    drawing = Drawing(width, height, transform=[
        width / (bounds[2] - bounds[0]), 0, 0, height / (bounds[3] - bounds[1]),
        -bounds[0] * width / (bounds[2] - bounds[0]), -bounds[1] * height / (bounds[3] - bounds[1]),
    ])
    drawing.add(widget)
    pdf.setFillColor(white)
    pdf.rect(x - L.QR_QUIET, y - L.QR_QUIET, width + L.QR_QUIET * 2, height + L.QR_QUIET * 2, fill=1, stroke=0)
    renderPDF.draw(drawing, pdf, x, y)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 5.5)
    pdf.drawCentredString(x + width / 2, y + height + 6, "DO NOT WRITE HERE")
    return payload


def draw_team_id(pdf, boxes, label_baseline):
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(boxes[0][0], label_baseline, "TEAM ID - ONE CHARACTER PER BOX")
    for box_x, box_y, box_w, box_h in boxes:
        pdf.setStrokeColor(INK)
        pdf.setLineWidth(1.2)
        pdf.rect(box_x, box_y, box_w, box_h, fill=0, stroke=1)


def draw_answer_table(pdf, page):
    """Draw the answer table: one block for short puzzles, side by side for long ones."""
    blocks = page["blocks"]
    rows = page["answerRows"]
    per_column = page["rowsPerColumn"]
    x, _, width, _ = page["table"]

    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(x, page["tableTitleBaseline"], "WRITE YOUR SOLUTION")
    pdf.setFont("Helvetica", 7.5)
    pdf.setFillColor(MUTED)
    note = ("Print one character in each cell. Fill the first column, then the next."
            if len(blocks) > 1 else "Print one character in each cell. Do not combine columns.")
    pdf.drawRightString(x + width, page["tableTitleBaseline"], note)

    labels = ["#", "CAR", "DIR", "SPACES"] if len(blocks) > 2 else ["#", "CAR", "DIRECTION", "SPACES"]
    header_size = 6.5 if len(blocks) > 2 else 8
    for block in blocks:
        block_x, block_y, block_w, block_h = block["table"]
        header_x, header_y, header_w, header_h = block["header"]
        pdf.setFillColor(INK)
        pdf.rect(header_x, header_y, header_w, header_h, fill=1, stroke=0)
        for label, (cell_x, cell_w) in zip(labels, block["edges"]):
            centered_text(pdf, label, cell_x, header_y + header_h / 2 - 2.5, cell_w,
                          size=header_size, color=white)

        pdf.setStrokeColor(LINE)
        pdf.setLineWidth(0.8)
        for step in range(per_column + 1):
            line_y = block_y + step * page["rowHeight"]
            pdf.line(block_x, line_y, block_x + block_w, line_y)
        for cell_x, _ in block["edges"]:
            pdf.line(cell_x, block_y, cell_x, block_y + block_h)
        pdf.line(block_x + block_w, block_y, block_x + block_w, block_y + block_h)

    pdf.setFont("Helvetica", 7)
    for index, row in enumerate(rows):
        cell_x, cell_y, cell_w, cell_h = row["number"]
        centered_text(pdf, str(index + 1), cell_x, cell_y + cell_h / 2 - 2.5, cell_w,
                      font="Helvetica", size=6.5 if len(blocks) > 2 else 7, color=MUTED)


def draw_puzzle_page(pdf, manifest, puzzle, page, overlay=None):
    pdf.setFillColor(PAPER)
    pdf.rect(0, 0, L.PAGE_W, L.PAGE_H, fill=1, stroke=0)
    registration_marks(pdf)

    pdf.setFillColor(RED)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(L.MARGIN, 750, f"{manifest['title'].upper()} / {manifest['round'].upper()}")
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawString(L.MARGIN, page["titleBaseline"], f"Puzzle {puzzle['code']}")
    pdf.setFont("Helvetica", 8)
    pdf.setFillColor(MUTED)
    pdf.drawString(L.MARGIN + 1, 706, "Move the red X car through the exit. Record every move below in order.")

    badge_x, badge_y, badge_w, badge_h = page["pointsBadge"]
    pdf.setFillColor(YELLOW)
    pdf.roundRect(badge_x, badge_y, badge_w, badge_h, 3, fill=1, stroke=0)
    points = puzzle["points"]
    centered_text(pdf, f"{points} POINT{'S' if points != 1 else ''}", badge_x, badge_y + badge_h / 2 - 3, badge_w, size=8)

    draw_sheet_code(pdf, puzzle, page)
    draw_team_id(pdf, page["team"], page["teamLabelBaseline"])

    board_size = page["board"][2]
    draw_board(pdf, puzzle["cars"], page["board"], label_size=max(10, board_size * 0.057))
    labels = ", ".join(car["id"] for car in puzzle["cars"])
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 7.5)
    pdf.drawCentredString(L.PAGE_W / 2, page["boardCaptionBaseline"], f"Valid car labels for this puzzle: {labels}")

    draw_answer_table(pdf, page)

    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 6.5)
    pdf.drawString(L.MARGIN, 42, "One row per move, in CAPITAL letters. Write the number of spaces moved. Cross out mistakes completely and use the next row.")
    pdf.drawRightString(
        L.PAGE_W - L.MARGIN, 42,
        f"FORM GS-02 / {manifest['packetId']} / PUZZLE {puzzle['index']} OF {len(manifest['puzzles'])}",
    )
    if overlay is not None:
        overlay(pdf, puzzle, page)
    pdf.showPage()


def draw_key(pdf, manifest):
    per_page = 5
    for start in range(0, len(manifest["puzzles"]), per_page):
        chunk = manifest["puzzles"][start:start + per_page]
        pdf.setFillColor(PAPER)
        pdf.rect(0, 0, L.PAGE_W, L.PAGE_H, fill=1, stroke=0)
        pdf.setFillColor(RED)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(L.MARGIN, 750, "GRADER COPY / DO NOT DISTRIBUTE")
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica-Bold", 22)
        pdf.drawString(L.MARGIN, 722, f"Answer key {manifest['packetId']}")
        pdf.setFont("Helvetica", 8)
        pdf.setFillColor(MUTED)
        pdf.drawString(
            L.MARGIN + 1, 708,
            f"{len(manifest['puzzles'])} puzzles / {manifest['totalPoints']} points total. "
            "Any legal route that frees X earns full credit; the route below is a shortest one.",
        )

        top = 680.0
        for puzzle in chunk:
            block_h = 116.0
            board_rect = [L.MARGIN, top - block_h + 12, 92.0, 92.0]
            draw_board(pdf, puzzle["cars"], board_rect, label_size=7, exit_label=False)
            text_x = L.MARGIN + 116
            pdf.setFillColor(INK)
            pdf.setFont("Helvetica-Bold", 13)
            pdf.drawString(text_x, top - 12, f"{puzzle['code']}")
            pdf.setFont("Helvetica", 8)
            pdf.setFillColor(MUTED)
            pdf.drawString(
                text_x, top - 26,
                f"{levels.name(puzzle['level'])} / {puzzle['points']} points / "
                f"shortest {puzzle['shortestMoves']} moves / {puzzle['sheetRows']} answer rows",
            )
            pdf.setFillColor(INK)
            pdf.setFont("Courier-Bold", 10)
            route = "  ".join(puzzle["solution"])
            for offset, line in enumerate(wrap_monospace(route, 10, L.PAGE_W - L.MARGIN - text_x)):
                pdf.drawString(text_x, top - 44 - offset * 13, line)
            pdf.setStrokeColor(LINE)
            pdf.setLineWidth(0.6)
            pdf.line(L.MARGIN, top - block_h, L.PAGE_W - L.MARGIN, top - block_h)
            top -= block_h + 12

        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 6.5)
        pdf.drawRightString(L.PAGE_W - L.MARGIN, 42, f"FORM GS-02-KEY / {manifest['packetId']}")
        pdf.showPage()


def wrap_monospace(text, size, width, font="Courier-Bold"):
    words = text.split("  ")
    lines, current = [], ""
    for word in words:
        candidate = f"{current}  {word}" if current else word
        if stringWidth(candidate, font, size) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def resolve_pages(manifest: dict) -> dict:
    """Attach the sheet geometry each puzzle was printed with to the manifest."""
    for puzzle in manifest["puzzles"]:
        rows = L.row_count(puzzle["shortestMoves"])
        puzzle["sheetRows"] = rows
        puzzle["layout"] = L.layout(rows)
    manifest["form"] = "GS-02"
    return manifest


def render_packet(manifest: dict, packet_path: Path, key_path: Path | None = None) -> dict:
    resolve_pages(manifest)
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(packet_path), pagesize=letter, pageCompression=1)
    pdf.setTitle(f"{manifest['round']} - {manifest['packetId']} student packet")
    for puzzle in manifest["puzzles"]:
        draw_puzzle_page(pdf, manifest, puzzle, puzzle["layout"])
    pdf.save()

    if key_path is not None:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key = canvas.Canvas(str(key_path), pagesize=letter, pageCompression=1)
        key.setTitle(f"{manifest['round']} - {manifest['packetId']} answer key")
        draw_key(key, manifest)
        key.save()
    return manifest


def render_bytes(manifest: dict, kind: str = "packet") -> bytes:
    """Render a packet or its key straight to PDF bytes, touching no files.

    The generator page uses this: sheets carry their own puzzle in a QR code, so
    there is nothing worth storing on the server.
    """
    resolve_pages(manifest)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter, pageCompression=1)
    if kind == "key":
        pdf.setTitle(f"{manifest['round']} - {manifest['packetId']} answer key")
        draw_key(pdf, manifest)
    else:
        pdf.setTitle(f"{manifest['round']} - {manifest['packetId']} student packet")
        for puzzle in manifest["puzzles"]:
            draw_puzzle_page(pdf, manifest, puzzle, puzzle["layout"])
    pdf.save()
    return buffer.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a packet PDF from a manifest JSON file.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--key", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    render_packet(manifest, args.packet, args.key)
    print(args.packet)


if __name__ == "__main__":
    main()
