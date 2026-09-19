#!/usr/bin/env python3
"""Turn a browser-generated puzzle set into a printable PDF.

Shared by the local grading station and the deployed function, so a sheet laid
out on a laptop and one laid out on a server are the same sheet. Nothing is
stored: the puzzles arrive in the request, the PDF goes back in the response,
and each page carries its own puzzle in a QR square.
"""

from __future__ import annotations

import levels
import packet_pdf
import sheet_code


MAX_PUZZLES = 60


def clean_puzzles(raw: object) -> list[dict]:
    """Validate the puzzle list a browser asked us to lay out."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("Send at least one puzzle to print.")
    if len(raw) > MAX_PUZZLES:
        raise ValueError(f"That is more than {MAX_PUZZLES} puzzles; print them in batches.")
    puzzles = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Puzzle {index} is not readable.")
        cars = item.get("cars")
        if not isinstance(cars, list) or not cars:
            raise ValueError(f"Puzzle {index} has no cars.")
        level = str(item.get("level", "medium")).lower()
        if level not in levels.LEVELS:
            raise ValueError(f"Puzzle {index} has an unknown level {level!r}.")
        solution = [str(move).upper() for move in item.get("solution", []) if str(move).strip()]
        puzzle = {
            "index": index,
            "code": str(item.get("code") or f"GS-{index:03d}").upper(),
            "level": level,
            "points": max(0, min(99, int(item.get("points", 0)))),
            "shortestMoves": max(1, min(40, int(item.get("shortestMoves", len(solution) or 1)))),
            "cars": [{
                "id": str(car["id"]).upper(),
                "row": int(car["row"]),
                "col": int(car["col"]),
                "length": int(car["length"]),
                "orientation": str(car["orientation"]).upper(),
            } for car in cars],
            "solution": solution,
        }
        # sheet_code refuses anything that will not survive the QR round trip.
        sheet_code.encode(puzzle["code"], puzzle["points"], 8, puzzle["cars"])
        puzzles.append(puzzle)
    return puzzles


def build_sheets(data: dict) -> tuple[bytes, str]:
    """Lay out a packet or its answer key and hand back the PDF."""
    kind = "key" if str(data.get("kind", "packet")).lower() == "key" else "packet"
    puzzles = clean_puzzles(data.get("puzzles"))
    packet_id = str(data.get("packetId") or "GS-SHEETS").upper()[:40]
    manifest = {
        "packetId": packet_id,
        "title": str(data.get("title") or "Middle School Math Meet")[:80],
        "round": str(data.get("round") or "Gridlock Sprint")[:80],
        "puzzles": puzzles,
        "totalPoints": sum(puzzle["points"] for puzzle in puzzles),
    }
    filename = f"gridlock-{packet_id}-{'key' if kind == 'key' else 'sheets'}.pdf"
    return packet_pdf.render_bytes(manifest, kind), filename
