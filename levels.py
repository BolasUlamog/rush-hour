#!/usr/bin/env python3
"""The difficulty tiers, for the Python side of the app.

`puzzle_set.js` owns the bands and does the generating; this is the same list of
tier names so the server and the PDF renderer cannot fall behind it. They did
once: a tier was added to the generator and the server went on rejecting it as
"an unknown level", which broke building the PDF. levels.test.py now compares the
two lists so that cannot happen again.
"""

from __future__ import annotations

# Ordered easiest first; this is the order pages appear in a packet.
LEVELS = ("easy", "medium", "hard")

LEVEL_NAMES = {
    "easy": "Easy",
    "medium": "Medium",
    "hard": "Hard",
}

DEFAULT_POINTS = {"easy": 4, "medium": 8, "hard": 15}


def name(level: str) -> str:
    return LEVEL_NAMES.get(str(level).lower(), str(level))
