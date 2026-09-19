#!/usr/bin/env python3
"""Local grading server: serves the app and privately proxies handwriting images."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
MAX_REQUEST = 24 * 1024 * 1024
LOCAL_OCR_SOURCE = APP_DIR / "handwriting_ocr.swift"
LOCAL_OCR_BINARY = Path(tempfile.gettempdir()) / "gridlock-handwriting-ocr"

import image_input
import levels
import packet_pdf
import sheet_code

try:  # Answer-sheet scanning needs Pillow; sheet building still works without it.
    import sheet_scan

    SHEET_SCAN_ERROR = ""
except Exception as error:  # pragma: no cover - depends on the local interpreter
    sheet_scan = None
    SHEET_SCAN_ERROR = (
        f"Answer-sheet scanning is unavailable ({error}). Run the grading station with the "
        "project virtualenv: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )


def json_bytes(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


class GradingHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP_DIR), **kwargs)

    def end_headers(self) -> None:
        # Never let a grading laptop keep running a cached copy of the app after
        # the station has been updated.
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, status: int, value: object) -> None:
        body = json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/api/health":
            self.send_json(200, {
                "ready": True,
                "recognizer": "on-device Apple Vision + glyph model",
                "heic": image_input.register_formats(),
                "sheetScanning": sheet_scan is not None,
                "sheetScanningNote": SHEET_SCAN_ERROR,
            })
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path not in ("/api/scan-sheet", "/api/sheets"):
            self.send_json(404, {"error": "Not found."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST:
                raise ValueError("The request is missing or too large.")
            data = json.loads(self.rfile.read(length))

            if self.path == "/api/sheets":
                pdf, filename = build_sheets(data)
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", f'inline; filename="{filename}"')
                self.send_header("Content-Length", str(len(pdf)))
                self.end_headers()
                self.wfile.write(pdf)
                return

            self.send_json(200, scan_answer_sheet(data.get("image", "")))
        except (ValueError, image_input.ImageError) as error:
            self.send_json(400, {"error": str(error)})
        except Exception as error:
            self.send_json(500, {"error": f"Could not handle that request: {error}"})


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


def scan_answer_sheet(image: str) -> dict:
    """Read one photographed answer sheet. The sheet describes its own puzzle."""
    if sheet_scan is None:
        raise RuntimeError(SHEET_SCAN_ERROR)
    binary = sheet_scan.ensure_recognizer(LOCAL_OCR_SOURCE, LOCAL_OCR_BINARY)
    # The page may send a re-encoded JPEG or, when the browser cannot decode the
    # file itself, the original photo — HEIC straight off an iPhone included.
    photo = image_input.load_data_url(image)
    try:
        return sheet_scan.scan_sheet(photo, binary)
    except sheet_scan.ScanError as error:
        raise ValueError(str(error)) from error


def main() -> None:
    print("Handwriting reader: on-device — nothing leaves this Mac")
    if not image_input.register_formats():
        print("HEIC photos unsupported: pip install -r requirements.txt to add pillow-heif")
    if SHEET_SCAN_ERROR:
        print(f"\n{SHEET_SCAN_ERROR}")
    else:
        print("Answer-sheet scanning: ready (sheets carry their own puzzle, nothing is stored)")
    server = None
    port = 8765
    for candidate_port in range(8765, 8775):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate_port), GradingHandler)
            port = candidate_port
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError("Could not find an available local port from 8765 through 8774.")
    url = f"http://127.0.0.1:{port}"
    print(f"\nGridlock Sprint grading station is running at {url}")
    print("Press Control-C to stop it.\n")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nGrading station stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
