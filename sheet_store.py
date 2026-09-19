#!/usr/bin/env python3
"""Keep the scoreboard in a Google Sheet.

For a contest this is often nicer than a database: organisers can watch the
standings live in a tab they already know, fix a mistyped team by hand, and keep
the sheet afterwards. It is also the least setup — no database to provision.

It talks to an Apps Script web app bound to the spreadsheet (google_sheet/Code.gs),
which runs as the sheet's owner. So no Google credentials ever reach this app, and
no Google client libraries are needed either: this is plain urllib, which matters
when the whole deployment has to fit inside a function size limit.

Set SHEET_WEBHOOK_URL to the deployment URL and SHEET_TOKEN to the same secret
the script has. The interface matches scores.ScoreStore, so the rest of the app
does not care which is in use.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

TIMEOUT = 25          # Apps Script is not fast; a grader would rather wait than lose a score


class SheetError(RuntimeError):
    """The spreadsheet could not be reached or refused the request."""


class SheetStore:
    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = (url if url is not None else os.environ.get("SHEET_WEBHOOK_URL", "")).strip()
        self.token = (token if token is not None else os.environ.get("SHEET_TOKEN", "")).strip()
        self.reason = ""

    @property
    def kind(self) -> str:
        return "google sheet"

    @property
    def configured(self) -> bool:
        return self.url.startswith("https://")

    @property
    def shared(self) -> bool:
        return True          # one spreadsheet, however many volunteers

    @property
    def available(self) -> bool:
        if not self.configured:
            self.reason = "SHEET_WEBHOOK_URL is not set."
            return False
        try:
            self._call({"action": "standings", "contest": "__check__"})
        except SheetError as error:
            self.reason = str(error)
            return False
        return True

    def _call(self, payload: dict) -> dict:
        body = json.dumps({**payload, "token": self.token}).encode("utf-8")
        # Apps Script reads the raw body, and text/plain avoids the preflight
        # that it has no way to answer.
        request = urllib.request.Request(
            self.url, data=body, method="POST",
            headers={"Content-Type": "text/plain;charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                answer = json.loads(response.read() or "{}")
        except urllib.error.HTTPError as error:
            raise SheetError(f"the spreadsheet returned {error.code}") from error
        except urllib.error.URLError as error:
            raise SheetError(f"the spreadsheet could not be reached: {error.reason}") from error
        except json.JSONDecodeError as error:
            # A misconfigured deployment answers with Google's sign-in page.
            raise SheetError(
                "the spreadsheet did not answer with JSON. Check the web app is deployed "
                "with access set to Anyone."
            ) from error
        if not answer.get("ok", False):
            raise SheetError(answer.get("error") or "the spreadsheet refused the request")
        return answer

    # -- the same operations as scores.ScoreStore ------------------------- #

    def ensure_schema(self) -> None:
        return          # the Apps Script creates its tab and headers itself

    def save(self, entry: dict) -> None:
        self._call({"action": "save", **entry})

    def standings(self, contest: str) -> dict:
        answer = self._call({"action": "standings", "contest": contest})
        return {
            "contest": contest,
            "teams": answer.get("teams", []),
            "entries": answer.get("entries", []),
            "shared": True,
        }

    def clear(self, contest: str) -> int:
        return int(self._call({"action": "clear", "contest": contest}).get("cleared", 0))
