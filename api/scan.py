"""POST /api/scan — read one photographed answer sheet.

The readers are built at module load so they are reused across requests on a
warm function: loading the character model is the slow part.
"""

from __future__ import annotations

from _shared import JsonHandler

import glyph_reader
import image_input
import sheet_scan
import text_reader

GLYPHS = glyph_reader.GlyphReader()
WORDS = text_reader.TextReader()


class handler(JsonHandler):
    def do_POST(self) -> None:
        try:
            data = self.read_json()
            photo = image_input.load_data_url(data.get("image", ""))
            self.send_json(200, sheet_scan.scan_sheet(photo, GLYPHS, WORDS))
        except sheet_scan.ScanError as error:
            self.send_json(400, {"error": str(error)})
        except (ValueError, image_input.ImageError) as error:
            self.send_json(400, {"error": str(error)})
        except Exception as error:                      # pragma: no cover
            self.send_json(500, {"error": f"Could not read that sheet: {error}"})
