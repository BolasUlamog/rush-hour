#!/usr/bin/env python3
"""The self-describing code printed on every answer sheet.

A sheet carries its own puzzle, so grading needs nothing but the photograph: no
manifest, no packet list, no server state. The QR code on each page holds the
board, the number of answer rows the page was printed with, and what the puzzle
is worth. The grading station decodes it, solves the board itself to find the
shortest route, and then checks what the student wrote.

Payload, deliberately short and inside QR's alphanumeric character set so the
code stays low-density and easy to decode from a phone photo:

    GS1*<code>*<points>*<rows>*<cars>

where <cars> is a run of five-character car records, each one
<id><row><col><length><orientation>, for example X202H = car X, row 2, column 0,
length 2, horizontal.
"""

from __future__ import annotations

VERSION = "GS1"
SEPARATOR = "*"
CAR_WIDTH = 5
ORIENTATIONS = ("H", "V")


class SheetCodeError(ValueError):
    """The payload is not a Gridlock sheet code."""


def encode_cars(cars: list[dict]) -> str:
    parts = []
    for car in cars:
        identifier = str(car["id"]).upper()
        orientation = str(car["orientation"]).upper()
        if len(identifier) != 1 or not identifier.isalnum():
            raise SheetCodeError(f"Car label {car['id']!r} must be a single letter or digit.")
        if orientation not in ORIENTATIONS:
            raise SheetCodeError(f"Car {identifier} has orientation {orientation!r}.")
        for field in ("row", "col", "length"):
            value = int(car[field])
            if not 0 <= value <= 9:
                raise SheetCodeError(f"Car {identifier} has {field}={value}, which will not fit in the code.")
        parts.append(f"{identifier}{int(car['row'])}{int(car['col'])}{int(car['length'])}{orientation}")
    return "".join(parts)


def decode_cars(text: str) -> list[dict]:
    if not text or len(text) % CAR_WIDTH:
        raise SheetCodeError("The car list in this sheet code is truncated.")
    cars = []
    for start in range(0, len(text), CAR_WIDTH):
        identifier, row, col, length, orientation = text[start:start + CAR_WIDTH]
        if not (row.isdigit() and col.isdigit() and length.isdigit()):
            raise SheetCodeError(f"Car record {text[start:start + CAR_WIDTH]!r} is not readable.")
        if orientation not in ORIENTATIONS:
            raise SheetCodeError(f"Car {identifier} has orientation {orientation!r}.")
        cars.append({
            "id": identifier.upper(),
            "row": int(row),
            "col": int(col),
            "length": int(length),
            "orientation": orientation,
        })
    return cars


def encode(code: str, points: int, rows: int, cars: list[dict]) -> str:
    """Build the payload printed as a QR code on one sheet."""
    label = str(code).upper()
    if SEPARATOR in label:
        raise SheetCodeError(f"Puzzle code {code!r} cannot contain {SEPARATOR!r}.")
    return SEPARATOR.join([VERSION, label, str(int(points)), str(int(rows)), encode_cars(cars)])


def decode(payload: str) -> dict:
    """Read a payload back, raising SheetCodeError on anything unexpected."""
    text = (payload or "").strip().upper()
    fields = text.split(SEPARATOR)
    if len(fields) != 5:
        raise SheetCodeError("This is not a Gridlock sheet code.")
    version, code, points, rows, cars = fields
    if version != VERSION:
        raise SheetCodeError(
            f"This sheet was printed by a different version ({version!r}); reprint it from this app."
        )
    if not points.isdigit() or not rows.isdigit():
        raise SheetCodeError("The points or row count in this sheet code is unreadable.")
    return {
        "code": code,
        "points": int(points),
        "rows": int(rows),
        "cars": decode_cars(cars),
    }


if __name__ == "__main__":
    sample = [
        {"id": "X", "row": 2, "col": 0, "length": 2, "orientation": "H"},
        {"id": "A", "row": 3, "col": 3, "length": 3, "orientation": "V"},
    ]
    payload = encode("GS-M-004", 5, 10, sample)
    print(payload)
    assert decode(payload)["cars"] == sample
    assert decode(payload)["points"] == 5 and decode(payload)["rows"] == 10
    print("round trip OK")
