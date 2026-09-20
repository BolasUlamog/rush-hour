#!/usr/bin/env python3
"""Guard against two copies of the same fact drifting apart.

The generator in puzzle_set.js defines the difficulty tiers; server.py validates
against them and packet_pdf.py prints their names. When a tier was once added to
the generator only, the server rejected every puzzle at that tier as "an
unknown level" and building a PDF failed. This test compares the two lists and
then pushes one puzzle of each tier through the same path the browser uses.

It also checks the dependency lists: Vercel installs from pyproject.toml while
local development installs from requirements.txt, and a deployment missing a
package fails with a bare ModuleNotFoundError on the first request.

Run with the project virtualenv:  ./.venv/bin/python levels.test.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import levels
import sheet_builder

APP_DIR = Path(__file__).resolve().parent


def levels_from_javascript() -> list[str]:
    source = (APP_DIR / "public" / "puzzle_set.js").read_text()
    match = re.search(r"const LEVELS = \[(.*?)\];", source, re.DOTALL)
    if not match:
        raise AssertionError("puzzle_set.js no longer declares a LEVELS array")
    return re.findall(r'"([^"]+)"', match.group(1))


def requirement_names(text: str) -> set[str]:
    """Package names from a requirements.txt or a pyproject dependency list."""
    names = set()
    for line in text.splitlines():
        line = line.split("#")[0].strip().strip('",')
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!\[;]", line)[0].strip()
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


def check_dependencies() -> list[str]:
    """Every runtime package must be declared in both dependency lists."""
    requirements = (APP_DIR / "requirements.txt").read_text()
    pyproject = (APP_DIR / "pyproject.toml").read_text()
    block = re.search(r"dependencies = \[(.*?)\]", pyproject, re.DOTALL)
    if not block:
        return ["pyproject.toml declares no dependencies, so Vercel will install nothing"]

    deployed = requirement_names(block.group(1))
    # requirements.txt may carry development-only extras; there are none today.
    development = set()
    local = requirement_names(requirements) - development

    problems = []
    for missing in sorted(local - deployed):
        problems.append(f"{missing} is in requirements.txt but not in pyproject.toml, "
                        "so the deployment would not install it")
    for extra in sorted(deployed - local):
        problems.append(f"{extra} is in pyproject.toml but not in requirements.txt")
    print(f"{'ok ' if not problems else 'FAIL'} dependency lists agree: {len(deployed)} packages")
    return problems


def main() -> int:
    failures = check_dependencies()

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
            pdf, filename = sheet_builder.build_sheets({
                "kind": kind,
                "packetId": "GS-LEVELS",
                "puzzles": built["puzzles"],
            })
            ok = pdf.startswith(b"%PDF") and len(pdf) > 2000
            if not ok:
                failures.append(f"{kind}: got {len(pdf)} bytes that are not a PDF")
            print(f"{'ok ' if ok else 'FAIL'} built the {kind}: {len(pdf) / 1024:.0f} KB {filename}")
        except Exception as error:
            failures.append(f"{kind}: {error}")
            print(f"FAIL could not build the {kind}: {error}")

    print(f"\ntiers checked: {', '.join(f'{p['level']}={p['shortestMoves']} moves' for p in built['puzzles'])}")
    if failures:
        print("\ndetails:")
        for failure in failures:
            print(f"  - {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
