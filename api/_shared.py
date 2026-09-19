"""Glue between Vercel's function runtime and the app's own modules.

The modules live at the project root so the local macOS grading station and the
deployed functions run exactly the same code. Vercel imports `api/*.py` with the
function directory on the path, so the root is added here once.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAX_REQUEST = 24 * 1024 * 1024


class JsonHandler(BaseHTTPRequestHandler):
    """Shared request plumbing: JSON in, JSON or PDF out, errors as JSON."""

    def log_message(self, *args):        # keep Vercel's logs to our own messages
        return

    def _send(self, status: int, payload: bytes, content_type: str, filename: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'inline; filename="{filename}"')
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status: int, value: object) -> None:
        self._send(status, json.dumps(value).encode("utf-8"), "application/json")

    def send_pdf(self, payload: bytes, filename: str) -> None:
        self._send(200, payload, "application/pdf", filename)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_REQUEST:
            raise ValueError("The request is missing or too large.")
        return json.loads(self.rfile.read(length))
