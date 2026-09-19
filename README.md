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
the handwriting, and checks the student's moves against the rules. A grandmaster
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
| Easy | 3-6 moves | 2 | 1 |
| Medium | 7-12 moves | 4 | 1 |
| Hard | 13-20 moves | 8 | 2 |
| Grandmaster | 21-40 moves | 15 | 2-3 |

A move is one slide of one car, any distance — the convention Rush Hour uses.

Difficulty does **not** come from adding more cars. Measured over thousands of
boards, extra traffic makes puzzles *easier*, because a crowded board has fewer
legal moves; 18 cars produced shorter solutions than 12. It comes from where the
puzzle *starts*. `search.js` walks a board's entire state graph, measures every
reachable position's distance from the exit, and starts the puzzle at a far one.
Sampling random start positions — the obvious approach — tops out around 12
moves; searching for far positions reaches 40.

Only about 2% of boards support a 25-move-plus start, so the generator tries many
boards for one grandmaster puzzle; expect a few seconds each. Set a seed to
reproduce a set exactly.


**Print at 100% scale** — no "shrink to fit". The four black corner squares are
how a photo gets straightened, and the QR square has to stay clean.

There is also a command-line path, which writes the same sheets to disk:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python make_packet.py --easy 4 --medium 4 --hard 2 --grandmaster 1 \
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

Each written cell is then read twice, by two independent recognizers:

- **`models/glyphs.onnx`** — a small CNN trained on EMNIST handwriting, run through
  onnxruntime. It reads each cell against only the characters that column can
  hold, which is where nearly all of its accuracy comes from. On EMNIST's own
  test set: 89% across all 47 classes, but **99.0% on L/R/U/D**, **99.8% on the
  digits 1-5**, and **94.8% on car labels**.
- **Apple's on-device text recognizer** — good at directions written out as words
  (`LEFT`, `RIGHT`) and a useful second opinion. It is poor at lone handwritten
  characters, so cells are packed into compact text lines before it sees them.

When the two agree the row passes quietly. When they disagree, the more confident
reading is used and **the row is flagged for the grader** unless one reader is both
very sure and well ahead of the other. A cell only one recognizer could read is
trusted only if it was confident. Recognition may ask for help, but must never
report a different move as if it were certain.

Recognition is an assistant, not the judge: the grader confirms the text before
any points are awarded. Nothing is uploaded — it all runs on the Mac. For student
privacy, the sheets have no name field, only a team ID.

## Testing

```bash
node engine.test.js            # puzzle rules: parsing, legality, generation
.venv/bin/python scan.test.py  # prints sheets, fills them in, fakes photos, scans them back
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
multi-block tables: puzzle identified 9/9, team ID 9/9, 93 of 111 rows read
automatically, 18 flagged for review, 0 wrong without a warning.

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
python3 -m venv .venv-train && .venv-train/bin/pip install -r requirements-train.txt
.venv-train/bin/python train_glyph_model.py --data /path/to/emnist
```

`--data` is a folder of EMNIST's gzipped idx files, from
[NIST](https://www.nist.gov/itl/products-and-services/emnist-dataset) (`gzip.zip`)
or the per-file mirror at
[aurelienduarte/emnist](https://github.com/aurelienduarte/emnist/tree/master/gzip).
Training takes a few minutes and prints per-column accuracy. `--export-only`
re-exports the saved checkpoint without training again.

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
| `glyph_preprocess.py` | normalizes a cell into an EMNIST-shaped glyph |
| `train_glyph_model.py` | retrains and exports `models/glyphs.onnx` (dev only) |
| `handwriting_ocr.swift` | Apple Vision wrapper: text regions and QR decoding |
| `server.py` | serves the app, lays out sheets, scans photos |
| `make.js` | the generator page |
| `sheets.js` | the answer-sheet station and team tally |
| `fill_sheet.py` | test fixtures: fills a sheet and fakes a photo (dev only) |

A sheet states its own row count, so changing `sheet_layout.py` later does not
stop already-printed sheets from scanning.
