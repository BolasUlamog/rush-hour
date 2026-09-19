#!/usr/bin/env python3
"""The shared scoreboard, so several volunteers can grade at once.

Scores used to live in one browser's localStorage, which meant every grader saw
only their own. This keeps them in a database instead.

One row per (contest, team, puzzle), so grading the same sheet twice replaces the
score rather than adding to it — the same rule the app always had, now enforced
by a primary key instead of by hope. That also makes a retry after a flaky
connection harmless.

Set DATABASE_URL (or any of the POSTGRES_URL names Vercel's integrations use) to
a Postgres connection string and the scoreboard is shared by everyone. With none
set, scores go to a local SQLite file: that is genuinely shared when one machine
serves the room, and is what the tests use.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
    contest         TEXT    NOT NULL,
    team            TEXT    NOT NULL,
    puzzle          TEXT    NOT NULL,
    points_possible INTEGER NOT NULL,
    points_awarded  INTEGER NOT NULL,
    status          TEXT    NOT NULL,
    moves           TEXT    NOT NULL,
    moves_used      INTEGER,
    optimal         INTEGER,
    graded_by       TEXT,
    graded_at       TEXT    NOT NULL,
    PRIMARY KEY (contest, team, puzzle)
)
"""

UPSERT = """
INSERT INTO scores (contest, team, puzzle, points_possible, points_awarded, status,
                    moves, moves_used, optimal, graded_by, graded_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (contest, team, puzzle) DO UPDATE SET
    points_possible = excluded.points_possible,
    points_awarded  = excluded.points_awarded,
    status          = excluded.status,
    moves           = excluded.moves,
    moves_used      = excluded.moves_used,
    optimal         = excluded.optimal,
    graded_by       = excluded.graded_by,
    graded_at       = excluded.graded_at
"""

SELECT_ALL = """
SELECT team, puzzle, points_possible, points_awarded, status, moves,
       moves_used, optimal, graded_by, graded_at
FROM scores WHERE contest = ? ORDER BY team, puzzle
"""


class ScoreStore:
    """A tiny data layer over either Postgres or SQLite."""

    # Vercel's Postgres integrations each set their own variable name, so take
    # whichever is present rather than making someone rename one by hand.
    URL_VARIABLES = ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL",
                     "POSTGRES_URL_NON_POOLING", "NEON_DATABASE_URL")

    @classmethod
    def url_from_environment(cls) -> str:
        for name in cls.URL_VARIABLES:
            value = (os.environ.get(name) or "").strip()
            if value:
                return value
        return ""

    def __init__(self, url: str | None = None):
        self.url = (url if url is not None else self.url_from_environment()).strip()
        self.postgres = self.url.startswith(("postgres://", "postgresql://"))
        self.reason = ""
        self._ready = False

    @property
    def kind(self) -> str:
        return "postgres" if self.postgres else "sqlite"

    @property
    def serverless(self) -> bool:
        """On Vercel each request may land on a fresh instance with its own disk."""
        return bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

    @property
    def shared(self) -> bool:
        """True when every grader really is writing to the same place.

        Postgres always is. A SQLite file is too when one machine serves everyone
        — a laptop on the room's wifi with volunteers on their phones is a
        perfectly good way to run a contest. It is *not* shared on a serverless
        host, where each instance gets its own temporary disk, so that case has
        to be reported honestly rather than looking like a scoreboard.
        """
        return self.postgres or not self.serverless

    # -- connections ------------------------------------------------------- #

    def local_file(self) -> Path:
        """Where the SQLite file lives.

        A serverless deployment can only write to the temporary directory, so it
        goes there — per instance, which is exactly why `shared` reports False in
        that case and the app tells the grader to expect it.
        """
        override = (os.environ.get("GRIDLOCK_SCORES") or "").strip()
        if override:
            return Path(override)
        if self.serverless:
            return Path(tempfile.gettempdir()) / "gridlock-scores.sqlite3"
        return APP_DIR / "output" / "scores.sqlite3"

    def _connect(self):
        if self.postgres:
            import pg8000.dbapi

            parts = urllib.parse.urlparse(self.url)
            query = dict(urllib.parse.parse_qsl(parts.query))
            ssl_context = None
            if query.get("sslmode", "require") != "disable":
                import ssl

                ssl_context = ssl.create_default_context()
                # Managed Postgres commonly presents a certificate for a proxy
                # host, and the connection is inside the provider's network.
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            return pg8000.dbapi.connect(
                user=urllib.parse.unquote(parts.username or ""),
                password=urllib.parse.unquote(parts.password or ""),
                host=parts.hostname or "localhost",
                port=parts.port or 5432,
                database=(parts.path or "/postgres").lstrip("/"),
                ssl_context=ssl_context,
                timeout=15,
            )
        path = self.local_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=15)
        connection.execute("PRAGMA journal_mode=WAL")      # concurrent readers
        return connection

    def _sql(self, statement: str) -> str:
        # pg8000 uses %s placeholders; the statements are written with ?.
        return statement.replace("?", "%s") if self.postgres else statement

    def ensure_schema(self) -> None:
        if self._ready:
            return
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(SCHEMA)
            connection.commit()
        self._ready = True

    @property
    def available(self) -> bool:
        try:
            self.ensure_schema()
        except Exception as error:
            self.reason = f"{self.kind} is unavailable: {error}"
            return False
        return True

    # -- operations -------------------------------------------------------- #

    def save(self, entry: dict) -> None:
        """Record one graded sheet, replacing any earlier score for it."""
        self.ensure_schema()
        values = (
            entry["contest"], entry["team"], entry["puzzle"],
            int(entry.get("pointsPossible", 0)), int(entry.get("pointsAwarded", 0)),
            str(entry.get("status", ""))[:32], str(entry.get("moves", ""))[:2000],
            int(entry.get("movesUsed") or 0) or None,
            int(entry["optimal"]) if entry.get("optimal") is not None else None,
            str(entry.get("gradedBy", ""))[:64] or None,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(self._sql(UPSERT), values)
            connection.commit()

    def standings(self, contest: str) -> dict:
        """Per-team totals plus every graded sheet, for the live scoreboard."""
        self.ensure_schema()
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(self._sql(SELECT_ALL), (contest,))
            rows = cursor.fetchall()

        entries = [{
            "team": row[0], "puzzle": row[1], "pointsPossible": row[2], "pointsAwarded": row[3],
            "status": row[4], "moves": row[5], "movesUsed": row[6], "optimal": row[7],
            "gradedBy": row[8], "gradedAt": str(row[9]),
        } for row in rows]

        totals: dict[str, dict] = {}
        for entry in entries:
            team = totals.setdefault(entry["team"], {"team": entry["team"], "points": 0, "sheets": 0, "solved": 0})
            team["points"] += entry["pointsAwarded"]
            team["sheets"] += 1
            team["solved"] += 1 if entry["pointsAwarded"] > 0 else 0
        ordered = sorted(totals.values(), key=lambda row: (-row["points"], row["team"]))
        return {"contest": contest, "teams": ordered, "entries": entries, "shared": self.shared}

    def clear(self, contest: str) -> int:
        self.ensure_schema()
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(self._sql("DELETE FROM scores WHERE contest = ?"), (contest,))
            removed = cursor.rowcount
            connection.commit()
        return max(0, removed)
