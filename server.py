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
PUBLIC_DIR = APP_DIR / "public"
MAX_REQUEST = 24 * 1024 * 1024

import app as deployed_app
import image_input
import sheet_builder

try:  # Answer-sheet scanning needs Pillow; sheet building still works without it.
    import glyph_reader
    import sheet_scan
    import text_reader

    GLYPHS = glyph_reader.GlyphReader()
    WORDS = text_reader.TextReader()
    SHEET_SCAN_ERROR = ""
except Exception as error:  # pragma: no cover - depends on the local interpreter
    sheet_scan = None
    GLYPHS = WORDS = None
    SHEET_SCAN_ERROR = (
        f"Answer-sheet scanning is unavailable ({error}). Run the grading station with the "
        "project virtualenv: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )


def json_bytes(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


class GradingHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

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
        if self.path.startswith("/api/scores"):
            from urllib.parse import parse_qs, urlparse

            query = parse_qs(urlparse(self.path).query)
            contest = deployed_app.contest_name((query.get("contest") or ["default"])[0])
            self.send_json(200, deployed_app.STORE.standings(contest))
            return
        if self.path == "/api/health":
            self.send_json(200, {
                "ready": True,
                "readers": {
                    "glyphModel": bool(GLYPHS and GLYPHS.available),
                    "appleText": bool(WORDS and WORDS.available),
                    "appleTextNote": (WORDS.reason if WORDS and not WORDS.available else ""),
                },
                "heic": image_input.register_formats(),
                "sheetScanning": sheet_scan is not None,
                "sheetScanningNote": SHEET_SCAN_ERROR,
                "scoreboard": {
                    "kind": deployed_app.STORE.kind,
                    "shared": deployed_app.STORE.shared and deployed_app.STORE.available,
                    "note": deployed_app.STORE.reason,
                },
            })
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path not in ("/api/scan", "/api/sheets", "/api/score", "/api/scores/clear"):
            self.send_json(404, {"error": "Not found."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST:
                raise ValueError("The request is missing or too large.")
            data = json.loads(self.rfile.read(length))

            if self.path == "/api/score":
                deployed_app.STORE.save(deployed_app.clean_entry(data))
                self.send_json(200, deployed_app.STORE.standings(
                    deployed_app.contest_name(data.get("contest"))))
                return

            if self.path == "/api/scores/clear":
                contest = deployed_app.contest_name(data.get("contest"))
                self.send_json(200, {"cleared": deployed_app.STORE.clear(contest), "contest": contest})
                return

            if self.path == "/api/sheets":
                pdf, filename = sheet_builder.build_sheets(data)
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


def scan_answer_sheet(image: str) -> dict:
    """Read one photographed answer sheet. The sheet describes its own puzzle."""
    if sheet_scan is None:
        raise RuntimeError(SHEET_SCAN_ERROR)
    # The page may send a re-encoded JPEG or, when the browser cannot decode the
    # file itself, the original photo — HEIC straight off an iPhone included.
    photo = image_input.load_data_url(image)
    try:
        return sheet_scan.scan_sheet(photo, GLYPHS, WORDS)
    except sheet_scan.ScanError as error:
        raise ValueError(str(error)) from error


def main() -> None:
    print(f"Readers: glyph model {'ready' if GLYPHS and GLYPHS.available else 'MISSING'}, "
          f"Apple text recognizer {'ready' if WORDS and WORDS.available else 'unavailable'}")
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
