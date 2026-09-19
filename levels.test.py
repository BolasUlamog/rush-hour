#!/usr/bin/env python3
"""Guard against the Python and JavaScript sides of the app drifting apart.

The generator in puzzle_set.js defines the difficulty tiers; server.py validates
against them and packet_pdf.py prints their names. When the grandmaster tier was
added to the generator only, the server rejected every grandmaster puzzle as "an
unknown level" and building a PDF failed. This test compares the two lists and
then pushes one puzzle of each tier through the same path the browser uses.

Run with the project virtualenv:  ./.venv/bin/python levels.test.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import levels
import server

APP_DIR = Path(__file__).resolve().parent


def levels_from_javascript() -> list[str]:
    source = (APP_DIR / "puzzle_set.js").read_text()
    match = re.search(r"const LEVELS = \[(.*?)\];", source, re.DOTALL)
    if not match:
        raise AssertionError("puzzle_set.js no longer declares a LEVELS array")
    return re.findall(r'"([^"]+)"', match.group(1))


def main() -> int:
    failures = []

    javascript = levels_from_javascript()
    python = list(levels.LEVELS)
    if javascript != python:
        failures.append(f"puzzle_set.js has {javascript} but levels.py has {python}")
    print(f"{'ok ' if javascript == python else 'FAIL'} tier lists match: {python}")

    missing_names = [level for level in python if level not in levels.LEVEL_NAMES]
    if missing_names:
        failures.append(f"levels.py has no printable name for {missing_names}")
    print(f"{'ok ' if not missing_names else 'FAIL'} every tier has a printable name")

    # Generate one puzzle per tier, then lay it out exactly as the browser does.
    counts = {level: 1 for level in python}
    process = subprocess.run(
        ["node", str(APP_DIR / "generate_packet.js"), json.dumps({"seed": 31337, "counts": counts})],
        capture_output=True, text=True, cwd=str(APP_DIR), timeout=600,
    )
    if process.returncode != 0:
        print(f"FAIL generator: {process.stderr.strip()}")
        return 1
    built = json.loads(process.stdout)
    found = {puzzle["level"] for puzzle in built["puzzles"]}
    for level in python:
        if level not in found:
            failures.append(f"the generator produced no {level} puzzle")

    for kind in ("packet", "key"):
        try:
            pdf, filename = server.build_sheets({
                "kind": kind,
                "packetId": "GS-LEVELS",
                "puzzles": built["puzzles"],
            })
            ok = pdf.startswith(b"%PDF") and len(pdf) > 2000
            if not ok:
                failures.append(f"{kind}: the server returned {len(pdf)} bytes that are not a PDF")
            print(f"{'ok ' if ok else 'FAIL'} server built the {kind}: {len(pdf) / 1024:.0f} KB {filename}")
        except Exception as error:
            failures.append(f"{kind}: {error}")
            print(f"FAIL server refused to build the {kind}: {error}")

    print(f"\ntiers checked: {', '.join(f'{p['level']}={p['shortestMoves']} moves' for p in built['puzzles'])}")
    if failures:
        print("\ndetails:")
        for failure in failures:
            print(f"  - {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
