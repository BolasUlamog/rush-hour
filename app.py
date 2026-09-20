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
import scores
import sheet_builder

MAX_REQUEST = 24 * 1024 * 1024
STORE = scores.open_store()

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
        "scoreboard": {
            "kind": STORE.kind,
            # False means each grader is writing somewhere of their own, which
            # for several volunteers at once is not what anyone wants.
            "shared": STORE.shared and STORE.available,
            "note": STORE.reason,
        },
    }


def scan(payload: dict) -> dict:
    """Read one sheet out of an upload.

    An upload may be a photograph or a PDF, and a PDF may hold a whole stack off
    a copier, so the caller says which page it wants and is told how many there
    are. Reading every page in one request is not an option: a sheet takes several
    seconds and the function's ceiling is sixty.
    """
    if sheet_scan is None:
        raise RuntimeError(SCAN_ERROR)
    pages = image_input.load_data_url_pages(payload.get("image", ""))
    try:
        wanted = int(payload.get("page") or 0)
    except (TypeError, ValueError):
        raise ValueError("That page number is not a number.") from None
    if not 0 <= wanted < len(pages):
        raise ValueError(f"That upload has {len(pages)} page(s); page {wanted + 1} is not one of them.")
    result = sheet_scan.scan_sheet(pages[wanted], GLYPHS, WORDS)
    result["page"] = wanted
    result["pages"] = len(pages)
    return result


def contest_name(value: object) -> str:
    """Scores are scoped to a contest so two rounds cannot mix."""
    name = str(value or "default").strip().upper()[:40]
    return name or "DEFAULT"


def clean_entry(body: dict) -> dict:
    """Validate one graded sheet before it reaches the database."""
    team = str(body.get("team") or "").strip().upper()[:24]
    puzzle = str(body.get("puzzle") or "").strip().upper()[:40]
    if not team:
        raise ValueError("A team ID is required to save a score.")
    if not puzzle:
        raise ValueError("A puzzle code is required to save a score.")
    possible = max(0, min(999, int(body.get("pointsPossible") or 0)))
    awarded = max(0, min(possible, int(body.get("pointsAwarded") or 0)))
    return {
        "contest": contest_name(body.get("contest")),
        "team": team,
        "puzzle": puzzle,
        "pointsPossible": possible,
        "pointsAwarded": awarded,
        "status": body.get("status"),
        "moves": body.get("moves"),
        "movesUsed": body.get("movesUsed"),
        "optimal": body.get("optimal"),
        "gradedBy": body.get("gradedBy"),
    }


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

        if path == "/api/scores" and method == "GET":
            from urllib.parse import parse_qs

            contest = (parse_qs(environ.get("QUERY_STRING") or "").get("contest") or ["default"])[0]
            return json_reply("200 OK", STORE.standings(contest_name(contest)))

        if path == "/api/score" and method == "POST":
            body = read_body()
            STORE.save(clean_entry(body))
            return json_reply("200 OK", STORE.standings(contest_name(body.get("contest"))))

        if path == "/api/scores/clear" and method == "POST":
            body = read_body()
            contest = contest_name(body.get("contest"))
            return json_reply("200 OK", {"cleared": STORE.clear(contest), "contest": contest})

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
