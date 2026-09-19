#!/usr/bin/env python3
"""End-to-end check: print sheets, fill them in, fake photos, scan them back.

Sheets are self-describing, so the scanner is given nothing but the photograph:
every assertion below compares what came back against what was written on that
page, and the puzzle identity has to come out of the printed QR square.

Run with the project virtualenv:  ./.venv/bin/python scan.test.py
Every case is checked against what was written, not against the answer key, so a
student's wrong answer must come back wrong.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

import glyph_reader
import image_input
import line_reader
import packet_pdf
import sheet_layout
import sheet_scan
import text_reader
import fill_sheet

APP_DIR = Path(__file__).resolve().parent
WORK = APP_DIR / "tmp" / "scan-test"
MANIFEST = APP_DIR / "output" / "packets" / "GS-TEST" / "manifest.json"

# page, team, moves (None = the answer key), handwriting style, direction words.
# Pages 1-2 are easy, 3-4 medium, 5-6 hard (two answer blocks), 7-8 grandmaster
# (two or three blocks), so every table layout is exercised.
CASES = [
    (1, "7B", None, "bradley", False),
    (1, "12A", None, "comic", False),
    (2, "A3", None, "bradley", True),
    (3, "9", ["XR1", "BL2", "XR3"], "comic", False),   # a wrong answer must stay wrong
    (4, "7B", None, "bradley", False),
    (5, "MS14", None, "comic", False),
    (6, "3C", None, "bradley", False),
    (7, "7B", None, "comic", False),
    (8, "42", None, "bradley", False),
]

# Photos that must still be handled: page shot upside down, corner mark missing.
UPSIDE_DOWN = (2, "7B", None, "bradley", False)
MISSING_CORNER = (1, "7B", None, "bradley", False)


def build_packet() -> dict:
    if not MANIFEST.exists():
        subprocess.run(
            [sys.executable, str(APP_DIR / "make_packet.py"), "--easy", "2", "--medium", "2",
             "--hard", "2", "--grandmaster", "2", "--seed", "4242", "--id", "GS-TEST"],
            check=True, capture_output=True, cwd=str(APP_DIR),
        )
    manifest = json.loads(MANIFEST.read_text())
    if "layout" not in manifest["puzzles"][0]:
        packet_pdf.resolve_pages(manifest)
    return manifest


def main() -> int:
    manifest = build_packet()
    WORK.mkdir(parents=True, exist_ok=True)
    glyph_model = glyph_reader.GlyphReader()
    apple_text = text_reader.TextReader()
    print(f"readers: glyph model {'ready' if glyph_model.available else 'MISSING'}, "
          f"Apple text recognizer {'ready' if apple_text.available else 'unavailable'}")

    total = moves_right = pages_right = teams_right = 0
    rows_total = rows_auto = rows_flagged = rows_silent_wrong = 0
    failures = []
    for number, (page, team, moves, style, words) in enumerate(CASES, 1):
        puzzle = manifest["puzzles"][page - 1]
        expected = moves or list(puzzle["solution"])
        stem = WORK / f"case{number:02d}"
        pdf_path = stem.with_suffix(".pdf")
        fill_sheet.fill_page(manifest, puzzle, team, expected, style, seed=number * 17, words=words,
                             out_pdf=pdf_path)
        fill_sheet.rasterize(pdf_path, stem.with_suffix(".png"), dpi=200)
        photo = stem.with_name(stem.name + "-photo.jpg")
        fill_sheet.fake_photo(stem.with_suffix(".png"), photo, seed=number * 13)

        total += 1
        try:
            result = sheet_scan.scan_sheet(Image.open(photo), glyph_model, apple_text)
        except sheet_scan.ScanError as error:
            failures.append(f"case {number} (page {page}, {style}): scan failed — {error}")
            continue

        code_ok = result["puzzleCode"] == puzzle["code"] and result["points"] == puzzle["points"]
        team_ok = result["team"] == team.upper()
        moves_ok = result["moves"] == expected
        pages_right += code_ok
        teams_right += team_ok
        moves_right += moves_ok

        # The invariant that matters: recognition may give up on a row and ask for
        # help, but it must never report a different move as if it were certain.
        decoded = {(car["id"], car["row"], car["col"], car["length"], car["orientation"])
                   for car in result.get("cars", [])}
        printed = {(car["id"], car["row"], car["col"], car["length"], car["orientation"])
                   for car in puzzle["cars"]}
        if decoded != printed:
            failures.append(f"case {number}: the board decoded from the QR does not match what was printed")

        by_row = {row["row"]: row for row in result["rows"]}
        auto = silent_wrong = needs_help = 0
        for index, want in enumerate(expected, 1):
            row = by_row.get(index)
            if row is None:
                silent_wrong += 1
                failures.append(f"case {number}: row {index} ({want}) was not even seen as written-in")
            elif row["move"] == want and not row["needsReview"]:
                auto += 1
            elif row["needsReview"]:
                needs_help += 1
            else:
                silent_wrong += 1
                failures.append(f"case {number}: row {index} read as {row['move']} (want {want}) with no warning")
        extra = [row for row in result["rows"] if row["row"] > len(expected)]
        if extra:
            failures.append(f"case {number}: {len(extra)} blank rows were treated as answers")
        rows_total += len(expected)
        rows_auto += auto
        rows_flagged += needs_help
        rows_silent_wrong += silent_wrong

        ok = code_ok and team_ok and not silent_wrong and not extra
        blocks = sheet_layout.column_count(puzzle["sheetRows"])
        print(f"{'ok ' if ok else 'FAIL'} case {number}: page {page} {puzzle['level'][:4]} "
              f"{style}{'/words' if words else ''} {puzzle['sheetRows']}r/{blocks}c "
              f"code={result['puzzleCode']} team={result['team']!r} "
              f"rows {auto}/{len(expected)} read, {needs_help} flagged")
        if not code_ok:
            failures.append(f"case {number}: code {result['puzzleCode']} != {puzzle['code']}")
        if not team_ok:
            failures.append(f"case {number}: team {result['team']!r} != {team.upper()!r}")

    # An upside-down photo has to come back the right way round.
    page, team, moves, style, words = UPSIDE_DOWN
    puzzle = manifest["puzzles"][page - 1]
    expected = moves or list(puzzle["solution"])
    stem = WORK / "upside-down"
    fill_sheet.fill_page(manifest, puzzle, team, expected, style, seed=5, words=words,
                         out_pdf=stem.with_suffix(".pdf"))
    fill_sheet.rasterize(stem.with_suffix(".pdf"), stem.with_suffix(".png"), dpi=200)
    photo = stem.with_name("upside-down-photo.jpg")
    fill_sheet.fake_photo(stem.with_suffix(".png"), photo, seed=9, rotate180=True)
    try:
        flipped = sheet_scan.scan_sheet(Image.open(photo), glyph_model, apple_text)
        upside_ok = flipped["puzzleCode"] == puzzle["code"] and flipped["moves"] == expected
        print(f"{'ok ' if upside_ok else 'FAIL'} upside down: code={flipped['puzzleCode']} "
              f"moves={len(flipped['moves'])}/{len(expected)}")
        if not upside_ok:
            failures.append(f"upside down: code={flipped['puzzleCode']} moves={flipped['moves']} != {expected}")
    except sheet_scan.ScanError as error:
        upside_ok = False
        failures.append(f"upside down: scan failed — {error}")

    # A real photograph of a real printed sheet, filled in by hand and shot on a
    # phone with a shadow across half the page. Synthetic fixtures are evenly lit
    # and so never caught the failure this one did: shadowed blank cells reading
    # as solid ink, which turned empty rows into invented moves.
    real_ok = True
    real_photo = APP_DIR / "tests" / "real-sheet.jpg"
    if real_photo.exists():
        expected_route = ["AD1", "JR1", "CU1", "HL4", "FL3", "CD1", "JL1", "CD1", "AD2", "XR4"]
        read = sheet_scan.scan_sheet(image_input.load_path(real_photo), glyph_model, apple_text)
        struck = [row["row"] for row in read["rows"] if row["struck"]]
        invented = [row["row"] for row in read["rows"] if row["row"] > 11 and row["move"]]
        real_ok = (
            read["puzzleCode"] == "GS-M-001"
            and read["team"] == "BANANA"
            and read["moves"] == expected_route
            and struck == [5]          # row 5 is crossed out on the paper
            and not invented           # the blank rows must stay blank
        )
        print(f"{'ok ' if real_ok else 'FAIL'} real photo: code={read['puzzleCode']} "
              f"team={read['team']!r} rows={len(read['moves'])}/10 struck={struck}")
        if not real_ok:
            failures.append(
                f"real photo: code={read['puzzleCode']} team={read['team']!r} "
                f"moves={read['moves']} struck={struck} invented={invented}"
            )
    else:
        print("skip real photo: tests/real-sheet.jpg is not present")

    # A second real photo: a hard sheet over two answer blocks, fourteen rows
    # written by hand, one of them scratched out, and three left blank.
    struck_ok = True
    struck_photo = APP_DIR / "tests" / "real-sheet-struck.jpg"
    if struck_photo.exists():
        expected_route = ["MD2", "AD2", "CR1", "EU1", "JL1", "GR2", "FR2", "KU2",
                          "HU1", "LL2", "MD1", "AD1", "XR3"]
        read = sheet_scan.scan_sheet(image_input.load_path(struck_photo), glyph_model, apple_text)
        struck = [row["row"] for row in read["rows"] if row["struck"]]
        invented = [row["row"] for row in read["rows"] if row["row"] >= 15 and row["move"]]
        struck_ok = (
            read["puzzleCode"] == "GS-H-001"
            and read["team"] == "BANANA"
            and read["moves"] == expected_route
            and struck == [2]          # row 2 is scratched out on the paper
            and not invented           # rows 15-17 are blank and must stay blank
        )
        print(f"{'ok ' if struck_ok else 'FAIL'} real photo, struck row: code={read['puzzleCode']} "
              f"team={read['team']!r} rows={len(read['moves'])}/13 struck={struck}")
        if not struck_ok:
            failures.append(
                f"struck-row photo: code={read['puzzleCode']} team={read['team']!r} "
                f"moves={read['moves']} struck={struck} invented={invented}"
            )
    else:
        print("skip real photo, struck row: tests/real-sheet-struck.jpg is not present")

    # The deployed server has no Apple recognizer, so the path it actually runs
    # — character model plus the PP-OCR line reader — is checked here too.
    deployed_ok = True
    if real_photo.exists():
        class NoAppleText:
            available = False
            reason = "checking the deployed path"

            def read(self, *args, **kwargs):
                return []

        expected_route = ["AD1", "JR1", "CU1", "HL4", "FL3", "CD1", "JL1", "CD1", "AD2", "XR4"]
        lines = line_reader.LineReader()
        read = sheet_scan.scan_sheet(image_input.load_path(real_photo), glyph_model,
                                     NoAppleText(), lines=lines)
        correct = sum(1 for got, want in zip(read["moves"], expected_route) if got == want)
        used = [row for row in read["rows"] if row["move"] and not row["struck"]]
        silent = [row["row"] for row, want in zip(used, expected_route)
                  if row["move"] != want and not row["needsReview"]]
        deployed_ok = (
            lines.available
            and read["puzzleCode"] == "GS-M-001"
            and read["team"] == "BANANA"
            and correct >= 9          # the pair of readers manages 10 of 10
            and not silent
        )
        print(f"{'ok ' if deployed_ok else 'FAIL'} deployed path (no Apple recognizer): "
              f"team={read['team']!r} rows {correct}/{len(expected_route)} "
              f"via {read['source']}")
        if not deployed_ok:
            failures.append(
                f"deployed path: team={read['team']!r} correct={correct} silent_wrong={silent} "
                f"line reader available={lines.available}"
            )

    # An iPhone shoots HEIC by default, so that has to grade as-is.
    page, team, moves, style, words = UPSIDE_DOWN
    puzzle = manifest["puzzles"][page - 1]
    expected = moves or list(puzzle["solution"])
    heic_ok = False
    try:
        import pillow_heif

        source = Image.open(WORK / "upside-down.png").convert("RGB")
        heic_path = WORK / "iphone.heic"
        pillow_heif.from_pillow(source).save(heic_path, format="HEIF", quality=80)
        photo = image_input.load_path(heic_path)
        read = sheet_scan.scan_sheet(photo, glyph_model, apple_text)
        heic_ok = read["puzzleCode"] == puzzle["code"] and read["moves"] == expected
        print(f"{'ok ' if heic_ok else 'FAIL'} heic photo: code={read['puzzleCode']} "
              f"moves={len(read['moves'])}/{len(expected)}")
        if not heic_ok:
            failures.append(f"heic: code={read['puzzleCode']} moves={read['moves']} != {expected}")
    except ImportError:
        print("skip heic photo: pillow-heif is not installed")
        heic_ok = True
    except Exception as error:
        failures.append(f"heic: {error}")
        print(f"FAIL heic photo: {error}")

    # A photo missing a corner mark must fail with advice, not a traceback.
    page, team, moves, style, words = MISSING_CORNER
    puzzle = manifest["puzzles"][page - 1]
    stem = WORK / "cropped"
    fill_sheet.fill_page(manifest, puzzle, team, list(puzzle["solution"]), style, seed=3, words=words,
                         out_pdf=stem.with_suffix(".pdf"))
    fill_sheet.rasterize(stem.with_suffix(".pdf"), stem.with_suffix(".png"), dpi=200)
    photo = stem.with_name("cropped-photo.jpg")
    fill_sheet.fake_photo(stem.with_suffix(".png"), photo, seed=3, crop_corner=True)
    try:
        sheet_scan.scan_sheet(Image.open(photo), glyph_model, apple_text)
        cropped_ok = False
        failures.append("missing corner: the scan should have been refused")
    except sheet_scan.ScanError as error:
        cropped_ok = "corner" in str(error).lower()
        if not cropped_ok:
            failures.append(f"missing corner: unhelpful message — {error}")
    print(f"{'ok ' if cropped_ok else 'FAIL'} missing corner mark: refused with advice")

    print(f"\npuzzle identified {pages_right}/{total}   team id {teams_right}/{total}   "
          f"whole sheet read with no help {moves_right}/{total}")
    print(f"rows: {rows_auto}/{rows_total} read automatically, {rows_flagged} flagged for the grader, "
          f"{rows_silent_wrong} wrong without a warning")
    if failures:
        print("\ndetails:")
        for failure in failures:
            print(f"  - {failure}")
    passed = pages_right == total and teams_right == total and rows_silent_wrong == 0
    return 0 if (passed and upside_ok and cropped_ok and heic_ok and real_ok and struck_ok
                 and deployed_ok and not failures) else 1


if __name__ == "__main__":
    raise SystemExit(main())
