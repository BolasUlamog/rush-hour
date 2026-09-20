# Gridlock Sprint

A Rush Hour-style contest kit for a physical middle-school math meet. Two pages,
one idea: **every printed sheet carries its own puzzle**, so grading needs
nothing but a photograph.

| tab | what it does |
| --- | --- |
| **Make puzzles** | generate N solvable puzzles and print one sheet per puzzle |
| **Answer sheets** | photograph a filled sheet, read it, check it, tally the team |

Students solve the 3D-printed boards and write their route on the sheet.

## The self-describing sheet

Each page prints a QR square holding the board, the number of answer rows on
that page, and what the puzzle is worth:

```
GS1*GS-M-004*5*10*X202HA333VB422V…
     code   pts rows  cars (id,row,col,length,orientation)
```

So the grading station never needs a packet list, a database, or a manifest. It
decodes the square, **solves the board itself** to find the shortest route, reads
the handwriting, and checks the student's moves against the rules. A hard
sheet needs forty-odd answer rows, so its table splits into two or three
side-by-side blocks numbered in reading order; the scanner only walks the list of
cells and never needs to know how a sheet was laid out. A sheet can be
graded on any machine running this app, in any order, months later. Its position
on the page also tells the scanner which way up the photo is.

Nothing about a puzzle is stored server-side. `sheet_code.py` is the whole
contract.

## Make puzzles

1. Double-click `Start Grading Station.command`.
2. Open **Make puzzles**, choose how many of each tier, press **Generate**.
3. Download **Student sheets** (print these) and **Answer key** (grader copy).

Puzzles are generated in the browser by `engine.js` — the same code that grades
them — so a board can never be generated that the grader disagrees about. The
server is asked only to lay the chosen boards out as PDF pages.

### Difficulty

| tier | shortest solution | points | answer blocks |
| --- | --- | --- | --- |
| Easy | 7-12 moves | 4 | 1-2 |
| Medium | 13-20 moves | 8 | 2 |
| Hard | 21-40 moves | 15 | 2-3 |

A move is one slide of one car, any distance — the convention Rush Hour uses, and
the one its published move counts refer to. For scale, ThinkFun's own expert cards
run 20 to 50 moves and the hardest board that exists takes 51.

Difficulty does **not** come from adding more cars. Measured over thousands of
boards, extra traffic makes puzzles *easier*, because a crowded board has fewer
legal moves; 18 cars produced shorter solutions than 12. It comes from where the
puzzle *starts*. `search.js` walks a board's entire state graph, measures every
reachable position's distance from the exit, and starts the puzzle at a far one.
Sampling random start positions — the obvious approach — tops out around 12
moves; searching for far positions reaches 40.

Only about 2% of boards support a 25-move-plus start, so the generator tries many
boards for one hard puzzle; expect a few seconds each. Set a seed to reproduce a
set exactly. A full ten-page packet takes about fifteen seconds.

#### Length is not the same as difficulty

A thirty-move puzzle can still be a forced shuffle with nothing to work out. So
each tier also ranks the boards it finds on how much *thought* they demand
(`routeInsight` in `search.js`), using two measures taken over every state that
lies on some shortest solution:

- **Cone size** — how many states those are. A wide cone means many different
  shortest routes, so blundering forward tends to work; a narrow one means a
  single corridor that has to be found. At a fixed 16 moves this ranges from 21
  to 688 across boards, so it separates them sharply.
- **Retreats** — whether a shortest solution ever drives the red car *away* from
  the exit. This is the move solvers refuse to look for, and the thing that makes
  a puzzle feel like it needs insight rather than patience.

Two measures that look useful are deliberately **not** used. `trapRate` — the
share of legal moves that do not shorten the solution — sits near 0.80 on
essentially every board, so it cannot choose between them. Raw fork counts
correlate 0.87 with cone size, so maximizing forks would quietly select *wide*
cones, i.e. easier puzzles, while appearing to pick harder ones.

Move count still leads on the tiers that maximize it, because it is the standard
measure and it is what the tier's points promise; ranking on cone alone was
measured trading a 39-move board for a narrower 22-move one. Ranking this way
raised the hard tier from a mean of 24.1 moves to 28.6, and took the share of
puzzles that force the red car backwards from 94% to 100% (easy: 56% to 100%,
with its solution cone halved).


**Print at 100% scale** — no "shrink to fit". The four black corner squares are
how a photo gets straightened, and the QR square has to stay clean.

There is also a command-line path, which writes the same sheets to disk:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python make_packet.py --easy 4 --medium 4 --hard 2 \
    --id GS-2026-R1 --open
```

## Grade the sheets

1. Open **Answer sheets** and photograph a page, all four corner squares in frame.
   HEIC straight off an iPhone is fine, as are JPEG, PNG and WebP.
2. Press **Read this sheet**. It shows the puzzle, its points, and the shortest
   route it worked out for that board.
3. **Check the transcription against the paper.** Rows the readers were unsure
   about are listed under the moves.
4. Press **Grade & award points**.

A puzzle is all-or-nothing: any legal route that frees the X car earns the page's
points, anything else earns zero, and the result says whether the route was a
shortest one. Scores live in the browser; **Export CSV** writes team totals plus a
row per graded sheet. Re-grading the same team and puzzle replaces the old score.

## How a sheet is read

Photos are decoded by `image_input.py`, which reads HEIC through pillow-heif and
applies the EXIF rotation phones record instead of baking in. The page shrinks a
photo before upload when the browser can decode it, and otherwise sends the file
untouched — so a grader never has to convert anything by hand.

`sheet_scan.py` finds the four corner squares, undoes the camera's tilt and
perspective onto a flat page, decodes the QR square, then measures ink in every
answer cell to see which rows were used.

Before any of that it **flattens the lighting**. A phone photo of a sheet is
rarely evenly lit, and on a real test photo a shadow across half the page dragged
blank paper down to brightness 100 while the lit paper sat at 211 — so a single
global ink threshold read shadowed *blank* cells as solid ink and invented moves
out of them. The paper level is now estimated locally and divided out, which
fixes it at the source and also hands both readers a properly contrasted crop.

A row scribbled out is honoured rather than guessed at: the sheet tells students
to cross a mistake out and use the next row, so a heavily inked cell that no
reader can turn into a confident move drops out of the route and is reported. An upside-down photo is straightened
automatically. A photo missing a corner square, or with an unreadable QR, is
refused with an explanation rather than guessed at.

Sheets arrive as photographs or as **PDFs**. A PDF is the one input that can hold
a whole pile: a volunteer running the stack through a copier gets back a single
file with thirty sheets in it, and those pages are far cleaner than any phone
photo — the three-sheet fixture in `scan.test.py` reads every move with nothing
flagged. Pages are rendered with pypdfium2, whose wheel is self-contained, because
the deployment cannot install a poppler binary.

Each sheet still has to be checked against the paper by a person, so a stack is
walked one sheet at a time rather than read in one go; `/api/scan` takes a `page`
and answers with `pages`. Reading a whole pile in one request is not an option
anyway — a sheet takes several seconds and the function's ceiling is sixty.

Each written cell is then read twice, by two independent recognizers:

- **`models/glyphs.onnx`** — a small CNN trained on EMNIST handwriting, run through
  onnxruntime. It reads each cell against only the characters that column can
  hold, which is where nearly all of its accuracy comes from. On EMNIST's own
  test set: 90% across all 47 classes, but **99.7% on L/R/U/D**, **99.9% on the
  digits 1-5**, and **98.7% on car labels**.

  It is also trained on *faint* glyphs, which is what the photographs actually
  contain. `glyph_preprocess.prepare()` fixes a black point off the cell's own
  histogram but never a white point, so lightly pencilled ink reaches the model
  dim — light pencil on white paper normalizes to a glyph peaking near 80/255,
  where every stock EMNIST glyph peaks at 255. Dimming, blurring, thinning and
  breaking the training glyphs is therefore not generic regularization; it is the
  distribution the grading station feeds it. Measured on a dimmed copy of the same
  EMNIST test set, against the same network trained without it:

  | on faintly pencilled glyphs | without | with |
  | --- | --- | --- |
  | all 47 classes | 39.9% | **87.5%** |
  | direction (L/R/U/D) | 74.2% | **99.1%** |
  | spaces (1-5) | 73.2% | **99.5%** |
  | car label (X, A-N) | 54.8% | **96.6%** |
- **A second reader, for an independent opinion.** Both are poor at a lone
  handwritten character, so cells are packed into compact text lines first.
  - On a Mac: **Apple's on-device text recognizer**.
  - Everywhere else: **`models/ppocr_rec.onnx`**, PaddleOCR's PP-OCR recognition
    model, run through the same onnxruntime. RapidOCR packages these models well
    but depends on OpenCV, which is 119 MB installed — more than the room left
    inside Vercel's 250 MB function limit. Its *detection* stage is what needs
    OpenCV, and this app never needs detection: it composes the digest itself, so
    it already knows where every line is. Only the recognition model is used, and
    it carries its own character dictionary in its ONNX metadata.

Measured on the two real photos in `tests/`, over 23 handwritten rows:

| readers | rows right | team IDs | rows flagged |
| --- | --- | --- | --- |
| character model alone | 17/23 | both wrong | 27 |
| + Apple recognizer | 23/23 | both right | 11 |
| + PP-OCR recognizer | 22/23 | both right | 11 |

So the deployed server reads within one row of a Mac. Losing the second reader
entirely is what costs real accuracy, not which of the two it is.

When the two agree the row passes quietly. When they disagree, the more confident
reading is used and **the row is flagged for the grader** unless one reader is both
very sure and well ahead of the other. A cell only one recognizer could read is
trusted only if it was confident. Recognition may ask for help, but must never
report a different move as if it were certain.

Recognition is an assistant, not the judge: the grader confirms the text before
any points are awarded. Nothing is uploaded — it all runs on the Mac. For student
privacy, the sheets have no name field, only a team ID.

## Deployment

The app runs on Vercel as well as on a Mac: `app.py` is a single WSGI entrypoint
serving `/api/health`, `/api/scan` and `/api/sheets`, and `public/` is the static
site. `server.py` is the local station and is excluded from the deployment.

Dependencies are declared in **both** `pyproject.toml` (which Vercel installs
from) and `requirements.txt` (for local work); `levels.test.py` checks the two
lists agree, because a deployment missing a package fails with a bare
ModuleNotFoundError on the first request.

The function bundle measures about 197 MB of Vercel's 250 MB limit, most of it
onnxruntime, numpy and the two models. Expect roughly 10 s on a cold start and
6 s warm, so `maxDuration` is set to 60 s.

## Several volunteers grading at once

Scores live in a database, one row per (round, team, puzzle), so grading the same
sheet twice replaces the score instead of adding to it — a retry after a dropped
connection cannot double-count. Each grader puts their name in, and the
scoreboard refreshes itself every few seconds.

Where those scores go depends on what is configured:

| setup | shared between volunteers? |
| --- | --- |
| `SHEET_WEBHOOK_URL` set to a Google Sheet | yes, and organisers can watch it live |
| `DATABASE_URL` set to a Postgres URL | yes, wherever it runs |
| one machine serving the room | yes — SQLite in `output/` |
| deployed with no database | **no**, each instance keeps its own |

### Scores in a Google Sheet

Often the nicest option for a contest: the standings sit in a tab everyone can
watch, a mistyped team can be fixed by hand, and the sheet is the record
afterwards. There is no database to provision and no Google credentials in the
app — an Apps Script bound to the spreadsheet does the writing, running as the
sheet's owner.

1. In the spreadsheet: **Extensions > Apps Script**, and paste
   [`google_sheet/Code.gs`](google_sheet/Code.gs).
2. Set `SHARED_TOKEN` in that script to a long random string.
3. **Deploy > New deployment > Web app**, *Execute as* **Me**, *Who has access*
   **Anyone**. Copy the deployment URL.
4. Give the app `SHEET_WEBHOOK_URL` (that URL) and `SHEET_TOKEN` (the same
   string). On Vercel those go in Settings > Environment Variables.

The script also reads a **TEAMS** tab for the tournament's acceptable team names,
looking anywhere on it so a list down a column or across a row both work. Those
names are what let a misread team ID be corrected: the list is closed, so a
scanned ID that is not on it is certainly wrong. `DANANA` becomes `BANANA`, and
the grader is told it happened. Anything not within a letter or two of exactly
one name is left as read, with the candidates named.

Without a spreadsheet the same list is read from [`teams.txt`](teams.txt), or
from `GRIDLOCK_TEAMS` as a comma separated list.

"Anyone" means anyone holding the URL can post to it, which is what the token is
for. Nothing else about the sheet is exposed.

That last case is reported in the app rather than looking like a scoreboard:
the panel says the scores are only on that device.

**For a contest in one room**, running `Start Grading Station.command` on a Mac
and having volunteers open its address is the best of both: one shared SQLite
scoreboard, and Apple's recognizer available as the second reader.

**On Vercel**, add Postgres under Storage in the project (Neon and Supabase both
work). The integration sets the connection variable itself, so nothing else
needs changing; `/api/health` then reports `scoreboard.shared: true`.

## Testing

```bash
node engine.test.js            # puzzle rules: parsing, legality, generation
.venv/bin/python scan.test.py  # prints sheets, fills them in, fakes photos, scans them back
.venv/bin/python scores.test.py # concurrent grading, replaced scores, separate rounds
.venv/bin/python levels.test.py # the two tier lists and the two dependency lists agree
```

`scan.test.py` renders filled sheets in two handwriting fonts, warps and shades
them like phone photos, and checks every row against **what was written**, not
against the answer key — a wrong answer has to come back wrong. It also checks
that the board decoded from the QR matches what was printed, and covers an
upside-down photo, a HEIC photo, one with a corner square out of frame, and
`tests/real-sheet.jpg` — an actual phone photo of a printed sheet, filled in by
hand, with a shadow over half the page and one row crossed out. Synthetic
fixtures are evenly lit, so that one photo catches a class of failure the
renders never could. The pass condition
is that no row is ever read wrongly without being flagged.

`engine.test.js` additionally holds `search.js` to the same answers as the
referee — if the fast solver disagreed, puzzles would ship with wrong answer keys
— and checks that a generated set lands inside its difficulty band.

Current result across all four tiers, including 19 to 25-move sheets with
multi-block tables: puzzle identified 9/9, team ID 9/9, 106 of 111 rows read
automatically, 5 flagged for review, 0 wrong without a warning.

Long sheets have smaller cells, so more rows get flagged than on an easy sheet.
A flagged row names the choice in front of the grader — "spaces read as 2, but
could be 1" — rather than just asking them to look again.

Those fixtures use handwriting *fonts*, which are not what the CNN was trained on,
so treat them as a floor rather than a prediction. Worth a dry run with real
printed sheets and real students before the meet.

## Retraining the character model

Only needed to change the alphabet or improve accuracy; the shipped
`models/glyphs.onnx` is ready to use and the grading station never needs PyTorch.

```bash
python3.13 -m venv .venv-train && .venv-train/bin/pip install -r requirements-train.txt
.venv-train/bin/python train_glyph_model.py --data /path/to/emnist
```

`--data` is a folder of EMNIST's gzipped idx files, from
[NIST](https://www.nist.gov/itl/products-and-services/emnist-dataset) (`gzip.zip`)
or the per-file mirror at
[aurelienduarte/emnist](https://github.com/aurelienduarte/emnist/tree/master/gzip)
— on that mirror the `raw.githubusercontent.com` URLs serve git-lfs pointer files,
so fetch from `media.githubusercontent.com/media/...` instead. Forty epochs takes
about fifteen minutes on an M-series laptop. `--export-only` re-exports the saved
checkpoint without training again.

Every accuracy it prints is measured twice, once on the stock test set and once on
a dimmed copy of it, because clean EMNIST accuracy says almost nothing about how a
photographed sheet will read. Per-column accuracy folds lowercase onto the capital
the way `glyph_reader` does when it reports a character: a capital `F` landing in
EMNIST's separate `f` class is not an error the grading station can make, and
counting it as one understates the car-label column by about four points.

`glyph_preprocess.py` is shared by training and inference so a cell is normalized
the same way in both; change it and you must retrain.

## Files

| file | role |
| --- | --- |
| `engine.js` | puzzle rules: generation, shortest-path solving, route validation |
| `search.js` | fast state-graph search: finds hard starting positions and shortest routes |
| `puzzle_set.js` | picks a set of distinct puzzles; shared by the page and the CLI |
| `sheet_code.py` | the QR payload that makes a sheet self-describing |
| `sheet_layout.py` | page geometry, shared by the renderer and the scanner |
| `packet_pdf.py` | draws the sheets and the answer key |
| `make_packet.py` | command-line path to a printed packet |
| `image_input.py` | decodes uploads, HEIC included, and fixes EXIF rotation |
| `sheet_scan.py` | photo to structured rows |
| `glyph_reader.py` | runs the character model over answer cells |
| `line_reader.py` | the cross-platform second reader (PP-OCR recognition) |
| `text_reader.py` | the second reader on a Mac (Apple Vision), when available |
| `glyph_preprocess.py` | normalizes a cell into an EMNIST-shaped glyph |
| `train_glyph_model.py` | retrains and exports `models/glyphs.onnx` (dev only) |
| `handwriting_ocr.swift` | Apple Vision wrapper: text regions and QR decoding |
| `server.py` | the local macOS grading station |
| `app.py` | the deployed API: one WSGI entrypoint for the same routes |
| `sheet_builder.py` | turns a generated puzzle set into a printable PDF |
| `scores.py` | the shared scoreboard, on Postgres or SQLite |
| `make.js` | the generator page |
| `sheets.js` | the answer-sheet station and team tally |
| `fill_sheet.py` | test fixtures: fills a sheet and fakes a photo (dev only) |

A sheet states its own row count, so changing `sheet_layout.py` later does not
stop already-printed sheets from scanning.
