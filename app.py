#!/usr/bin/env python3
"""The deployed app: one WSGI entrypoint for the whole API.

Vercel's Python runtime wants a single callable rather than a file per route, and
that suits this app: the character model takes a moment to load, so having one
function means one warm instance serves scanning and sheet building both.

Everything here is a thin shell over the same modules the local macOS grading
station uses (server.py), so a sheet graded on a laptop and one graded on the
server go through identical code.
"""

from __future__ import annotations

import json
import traceback

import image_input
import sheet_builder

MAX_REQUEST = 24 * 1024 * 1024

try:
    import glyph_reader
    import sheet_scan
    import text_reader

    GLYPHS = glyph_reader.GlyphReader()
    WORDS = text_reader.TextReader()
    SCAN_ERROR = ""
except Exception as error:                       # pragma: no cover
    GLYPHS = WORDS = sheet_scan = None
    SCAN_ERROR = f"Scanning is unavailable on this deployment: {error}"


def health() -> dict:
    return {
        "ready": bool(GLYPHS and GLYPHS.available),
        "readers": {
            "glyphModel": bool(GLYPHS and GLYPHS.available),
            "appleText": bool(WORDS and WORDS.available),
            # Apple's recognizer is macOS only, so this is expected to be off here.
            "appleTextNote": (WORDS.reason if WORDS and not WORDS.available else ""),
        },
        "heic": image_input.register_formats(),
        "scanning": sheet_scan is not None,
        "scanningNote": SCAN_ERROR,
    }


def scan(payload: dict) -> dict:
    if sheet_scan is None:
        raise RuntimeError(SCAN_ERROR)
    photo = image_input.load_data_url(payload.get("image", ""))
    return sheet_scan.scan_sheet(photo, GLYPHS, WORDS)


def app(environ, start_response):
    path = (environ.get("PATH_INFO") or "/").rstrip("/") or "/"
    method = (environ.get("REQUEST_METHOD") or "GET").upper()

    def reply(status: str, body: bytes, content_type: str, extra=()):
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            *extra,
        ]
        start_response(status, headers)
        return [body]

    def json_reply(status: str, value: object):
        return reply(status, json.dumps(value).encode("utf-8"), "application/json")

    def read_body() -> dict:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        if length <= 0 or length > MAX_REQUEST:
            raise ValueError("The request is missing or too large.")
        return json.loads(environ["wsgi.input"].read(length))

    try:
        if path == "/api/health" and method == "GET":
            return json_reply("200 OK", health())

        if path == "/api/scan" and method == "POST":
            return json_reply("200 OK", scan(read_body()))

        if path == "/api/sheets" and method == "POST":
            pdf, filename = sheet_builder.build_sheets(read_body())
            return reply("200 OK", pdf, "application/pdf",
                         [("Content-Disposition", f'inline; filename="{filename}"')])

        return json_reply("404 Not Found", {"error": f"No route for {method} {path}."})

    except (ValueError, image_input.ImageError) as error:
        return json_reply("400 Bad Request", {"error": str(error)})
    except Exception as error:
        if sheet_scan is not None and isinstance(error, sheet_scan.ScanError):
            return json_reply("400 Bad Request", {"error": str(error)})
        traceback.print_exc()
        return json_reply("500 Internal Server Error",
                          {"error": f"Could not handle that request: {error}"})


application = app          # some hosts look for this name instead
