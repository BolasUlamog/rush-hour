"""POST /api/sheets — lay out puzzles as a printable PDF.

Nothing is stored: every sheet carries its own puzzle in a QR square, so the
browser sends the boards it generated and gets a PDF back.
"""

from __future__ import annotations

from _shared import JsonHandler

import sheet_builder


class handler(JsonHandler):
    def do_POST(self) -> None:
        try:
            data = self.read_json()
            pdf, filename = sheet_builder.build_sheets(data)
            self.send_pdf(pdf, filename)
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except Exception as error:                      # pragma: no cover
            self.send_json(500, {"error": f"Could not build the sheets: {error}"})
