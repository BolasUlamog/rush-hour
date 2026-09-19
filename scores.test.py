#!/usr/bin/env python3
"""Check the shared scoreboard behaves when several volunteers grade at once.

Run with the project virtualenv:  ./.venv/bin/python scores.test.py

Uses a throwaway SQLite file. Set DATABASE_URL to run the same checks against a
real Postgres, which is what the deployment uses.
"""

from __future__ import annotations

import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

WORK = Path(tempfile.mkdtemp()) / "scores.sqlite3"
os.environ.setdefault("GRIDLOCK_SCORES", str(WORK))

import app                                    # noqa: E402  (after the env var)
import scores                                 # noqa: E402


def main() -> int:
    failures: list[str] = []
    store = scores.ScoreStore(os.environ.get("DATABASE_URL", ""))
    if not store.available:
        print(f"FAIL store unavailable: {store.reason}")
        return 1
    contest = "TEST-ROUND"
    store.clear(contest)
    print(f"store: {store.kind}, shared={store.shared}")

    def grade(volunteer: str, team: str, puzzle: str, awarded: int) -> None:
        store.save(app.clean_entry({
            "contest": contest, "team": team, "puzzle": puzzle,
            "pointsPossible": 8, "pointsAwarded": awarded,
            "status": "success" if awarded else "error",
            "moves": "MD2 AD2 XR3", "movesUsed": 3, "optimal": 13, "gradedBy": volunteer,
        }))

    # Four volunteers grading different sheets at the same moment.
    work = [(f"volunteer-{i}", team, f"GS-H-{i:03d}", 8)
            for i, team in enumerate(["BANANA", "APPLE", "CHERRY", "DATE"], 1)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda job: grade(*job), work))
    standings = store.standings(contest)
    concurrent_ok = len(standings["entries"]) == 4 and len(standings["teams"]) == 4
    print(f"{'ok ' if concurrent_ok else 'FAIL'} four volunteers at once: "
          f"{len(standings['entries'])} sheets, {len(standings['teams'])} teams")
    if not concurrent_ok:
        failures.append(f"concurrent writes produced {len(standings['entries'])} sheets, wanted 4")

    # Re-grading the same sheet replaces the score; a retry cannot double-count.
    grade("volunteer-1", "BANANA", "GS-H-001", 0)
    grade("volunteer-9", "BANANA", "GS-H-001", 8)
    standings = store.standings(contest)
    banana = next(row for row in standings["teams"] if row["team"] == "BANANA")
    entry = next(e for e in standings["entries"] if e["team"] == "BANANA" and e["puzzle"] == "GS-H-001")
    replace_ok = banana["points"] == 8 and banana["sheets"] == 1 and entry["gradedBy"] == "volunteer-9"
    print(f"{'ok ' if replace_ok else 'FAIL'} re-grading replaces: BANANA has {banana['points']} points "
          f"over {banana['sheets']} sheet(s), last graded by {entry['gradedBy']}")
    if not replace_ok:
        failures.append(f"re-grading gave {banana} / {entry['gradedBy']}")

    # Awarded points can never exceed what the puzzle is worth.
    try:
        app.clean_entry({"contest": contest, "team": "X", "puzzle": "P", "pointsPossible": 5,
                         "pointsAwarded": 500})
        clamp_ok = app.clean_entry({"contest": contest, "team": "X", "puzzle": "P",
                                    "pointsPossible": 5, "pointsAwarded": 500})["pointsAwarded"] == 5
    except Exception as error:
        clamp_ok = False
        failures.append(f"clamping points raised {error}")
    print(f"{'ok ' if clamp_ok else 'FAIL'} an over-large award is clamped to the puzzle's value")

    # A missing team ID must be refused rather than stored as a blank team.
    refused = False
    try:
        app.clean_entry({"contest": contest, "team": "  ", "puzzle": "P"})
    except ValueError:
        refused = True
    print(f"{'ok ' if refused else 'FAIL'} a score with no team ID is refused")
    if not refused:
        failures.append("a blank team ID was accepted")

    # Rounds are separate scoreboards.
    grade("volunteer-1", "BANANA", "GS-H-001", 8)
    store.save(app.clean_entry({"contest": "OTHER-ROUND", "team": "BANANA", "puzzle": "GS-H-001",
                                "pointsPossible": 8, "pointsAwarded": 2, "status": "success",
                                "moves": "XR1", "movesUsed": 1, "optimal": 1, "gradedBy": "v"}))
    separate_ok = (store.standings(contest)["teams"][0]["points"] == 8
                   and store.standings("OTHER-ROUND")["teams"][0]["points"] == 2)
    print(f"{'ok ' if separate_ok else 'FAIL'} rounds are kept apart")
    if not separate_ok:
        failures.append("scores leaked between rounds")

    removed = store.clear(contest)
    cleared_ok = removed == 4 and not store.standings(contest)["entries"]
    print(f"{'ok ' if cleared_ok else 'FAIL'} clearing a round removed {removed} sheets "
          f"and left the other round alone")
    if not cleared_ok:
        failures.append(f"clear removed {removed}")
    store.clear("OTHER-ROUND")

    if failures:
        print("\ndetails:")
        for failure in failures:
            print(f"  - {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
