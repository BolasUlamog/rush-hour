"""GET /api/health — what this deployment can do."""

from __future__ import annotations

from _shared import JsonHandler   # noqa: F401  (also puts the project root on the path)

import glyph_reader
import image_input
import text_reader

GLYPHS = glyph_reader.GlyphReader()
WORDS = text_reader.TextReader()


class handler(JsonHandler):
    def do_GET(self) -> None:
        self.send_json(200, {
            "ready": GLYPHS.available,
            "readers": {
                "glyphModel": GLYPHS.available,
                "appleText": WORDS.available,
                "appleTextNote": "" if WORDS.available else WORDS.reason,
            },
            "heic": image_input.register_formats(),
        })
