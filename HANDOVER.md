# Gridlock Sprint — handover

Rush Hour contest kit for a middle-school math meet. Two halves: generate puzzles
and print sheets; photograph filled sheets and tally teams.

- Repo: https://github.com/BolasUlamog/rush-hour (public, `main`, auto-deploys)
- Live: https://rush-hour-wheat.vercel.app
- Vercel project: https://vercel.com/damian-395f/rush-hour
- Code lives in `rush-hour-challenge/`. **Never `git init` the parent folder** —
  it holds an unrelated Discord-scraping project with member-list CSVs, and this
  repo is public.

## The one idea to understand first

**Every printed sheet carries its own puzzle in a QR square** (`sheet_code.py`):

```
GS1*GS-M-004*5*10*X202HA333VB422V…
     code   pts rows  cars (id,row,col,length,orientation)
```

So grading needs nothing but the photograph — no manifest, no packet list, no
server state. The scanner decodes that square, solves the board itself to get the
optimal move count, reads the handwriting, and checks the student's route. A sheet
can be graded on any machine, in any order, months later. The QR's position on
the page also tells the scanner which way up the photo is.

## Reading a sheet

`sheet_scan.py`: find the four corner squares → undo perspective → flatten the
lighting → decode the QR → measure ink per cell → read the cells.

Two independent readers, which is the important design point:

| reader | where | strength |
| --- | --- | --- |
| `glyph_reader.py` — small CNN on EMNIST | everywhere | reads each cell against only the characters that column can hold: 99.7% on L/R/U/D, 99.9% on digits 1-5, 98.7% on car labels — and, because it is trained on dimmed glyphs, 99.1% / 99.5% / 96.6% on faint ones |
| `text_reader.py` — Apple Vision | macOS only | whole words, e.g. `LEFT` |
| `line_reader.py` — PP-OCR recognition | everywhere else | same role off a Mac, within one row of Apple's |

When the two agree a row passes quietly; when they disagree the row is flagged.
**The invariant the tests enforce: recognition may ask for help, but must never
report a different move as if it were certain.** Losing the second reader entirely
costs real accuracy (17/23 rows instead of 23/23 on the real photos), which is why
the PP-OCR one exists.

Measured on the two flat real photos: 23 of 23 rows, both team IDs right.

The character model is trained on *faint* glyphs, and that is most of why it
works. `glyph_preprocess.prepare()` fixes a black point off the cell's own
histogram but never a white point, so light pencil reaches the model dim — it
normalizes to a glyph peaking near 80/255 where every stock EMNIST glyph peaks at
255. The same network trained without that augmentation scores 39.9% on a dimmed
test set against 87.5% with it, and 54.8% against 96.6% on car labels. On clean
glyphs the two are indistinguishable, which is exactly why the clean number was
never the one to chase.

## Scoreboard

`scores.open_store()` picks, in order: Google Sheet → Postgres → SQLite. All three
behave the same to the rest of the app (`save`, `standings`, `clear`, `team_names`).
One row per (round, team, puzzle), so re-grading **replaces** rather than adds — a
retry after a dropped connection cannot double-count.

`shared` is reported honestly: a SQLite file really is shared when one machine
serves the room, and is not on a serverless host where each instance has its own
disk. The panel says which.

**The Google Sheet is connected and live.** The Apps Script web app is deployed
against Damian's
[rush hour scoring](https://docs.google.com/spreadsheets/d/1dz3JvHJVnyB9i_ex8BslAvjRy7Ozv4wJ2HvjZV_4j58/edit)
sheet, and `SHEET_WEBHOOK_URL` / `SHEET_TOKEN` are set on Vercel, so
`/api/health` reports `{"kind": "google sheet", "shared": true}` and the roster
comes from the sheet's TEAMS tab rather than `teams.txt`. Verified end to end:
save, re-grade replacing rather than duplicating, clear, and team names.

**Those two values must never be committed — this repo is public.** They live in
Vercel's environment variables only. Redeploying the Apps Script mints a *new*
URL, so `SHEET_WEBHOOK_URL` has to be updated whenever that happens, followed by
a Vercel redeploy: environment changes do not reach a running deployment.

Testing the endpoint by hand has one trap. Apps Script answers `/exec` with a 302
whose body must be fetched by GET, so `curl -X POST -L` forces POST onto the
redirect and gets HTTP 405 behind a "Page Not Found" page that looks exactly like
a broken deployment. Omit `-X POST`. `sheet_store.py` uses urllib, which already
downgrades correctly.

**Team IDs are corrected against the tournament's closed list of names**
(`teams.txt`, or the sheet's TEAMS tab): `DANANA` → `BANANA`, always reported,
never applied when ambiguous.

## Deployment

`app.py` is one WSGI entrypoint (`/api/health`, `/api/scan`, `/api/sheets`,
`/api/score`, `/api/scores`, `/api/scores/clear`); `public/` is the static site;
`server.py` is the local macOS station and is excluded from the deployment.

- Dependencies are declared in **both** `pyproject.toml` (Vercel installs from it)
  and `requirements.txt`. `levels.test.py` checks they agree — a deployment missing
  a package fails with a bare `ModuleNotFoundError` on the first request.
- Bundle is ~197 MB of Vercel's 250 MB limit. Cold start ~10 s, warm ~6 s,
  `maxDuration` 60 s.
- Difficulty tiers exist in `public/puzzle_set.js` **and** `levels.py`;
  `levels.test.py` checks those agree too. A stale copy of that list once made the
  server reject every grandmaster puzzle.

## Tests

```bash
node engine.test.js              # puzzle rules; also holds search.js to the same answers
.venv/bin/python scan.test.py    # 9 synthetic sheets + 3 real photos + HEIC + upside down + missing corner
.venv/bin/python scores.test.py  # concurrent grading, replaced scores, rounds, sheet protocol, roster
.venv/bin/python levels.test.py  # the two tier lists and the two dependency lists agree
```

All passing. `scan.test.py`: puzzle identified 9/9, team ID 9/9,
106 of 111 rows read automatically, 5 flagged, **0 wrong without a warning**.

The three real photos in `tests/` are the valuable ones — synthetic renders are
evenly lit and never caught the bugs the real photos did (a shadow making blank
cells read as ink; a dark bedspread merging a corner mark into the background).

## What's next, roughly in order

1. **Discord login for volunteers** — wanted so server roles decide who can
   grade. **Blocked on the tournament's tech team, by decision:** the Discord
   permissions and roles already exist, so this should plug into that rather than
   grow a second login of its own. Nothing implemented, and nothing should be
   until that conversation happens — building a parallel auth system first is the
   specific outcome being avoided.
2. Person IDs on sheets stay manual by decision. Teams can be mixed groups, so a
   team name does not imply a fixed set of people.
3. A dry run with real printed sheets and real students before the meet. The
   synthetic fixtures use handwriting *fonts*, so their numbers are a floor, not a
   prediction, and only three genuine photographs exist to test against.

## Things already tried and rejected — don't redo them

- **Per-cell OCR with Apple Vision.** It will not read an isolated character in a
  wide ruled box. Cells are packed into compact text lines first; the gap between
  them is a real tuning knob (20px).
- **Snapping answer rows onto the printed table rules**, to correct a bowed page.
  Measured across all three real photos it was a wash, and one variant produced an
  unflagged wrong reading. Also tried and dropped: a smaller cell inset, a more
  sensitive ink threshold, a wider flat-field window, following glyphs past the
  rule.
- **RapidOCR as a package.** It pulls in OpenCV, 119 MB installed, which does not
  fit. Only PP-OCR's recognition model is used, driven directly — detection is
  unnecessary because the digest is composed here, so line positions are known.
- **More cars to make puzzles harder.** Measured over thousands of boards, extra
  traffic makes puzzles *easier*: a crowded board has fewer legal moves. Difficulty
  comes from `search.js` walking the state graph and starting far from the exit.
- **Modelling a skipping pencil as a rectangular cutout** in the training
  augmentation. It removed a third of a glyph's width in one clean block, which
  taught the model to invent whatever shape was missing: it then read a plain `1`
  on `tests/real-sheet.jpg` as a `2` and was confident enough about it to override
  Apple. A smooth low-frequency dimming field — thin irregular gaps, like real
  pencil on grained paper — scores better on every EMNIST measure *and* leaves the
  real photos correct.

## Known limitation worth remembering

Agreement between the two readers is the confidence signal, so when both misread
the same faint character nothing flags it. `tests/real-sheet-curled.jpg` is that
case (row 7, a faint `2` read as `1` by both; retraining took that sheet from 18
of 21 rows to 19, but not this row). The safety net is one level up: the
route then fails validation naming the move, so the grader is pointed at it rather
than a team being silently scored zero.
