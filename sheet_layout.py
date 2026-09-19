#!/usr/bin/env python3
"""Fixed page geometry shared by the packet renderer and the sheet scanner.

Every rectangle is ``[x, y, width, height]`` in PDF points with the origin at
the bottom-left corner of a US Letter page. ``layout()`` is the single source of
truth: ``packet_pdf`` draws with it and ``sheet_scan`` crops with it. The result
is also stored in each packet manifest, so a sheet printed by an older version
of this file still scans correctly after the layout changes.
"""

from __future__ import annotations

PAGE_W = 612.0
PAGE_H = 792.0
MARGIN = 42.0

MARK_SIZE = 11.0
MARK_INSET = 24.0

TEAM_BOX = 28.0
TEAM_GAP = 4.0
TEAM_BOXES = 6
TEAM_Y = 640.0

TABLE_BOTTOM = 62.0
HEADER_H = 26.0
ROW_H_MIN = 20.0
ROW_H_MAX = 32.0
COLUMNS = (30.0, 130.0, 200.0, 168.0)  # number, car, direction, spaces
MIN_ROWS = 8
MAX_ROWS = 48
ROWS_PER_COLUMN = 16      # beyond this the answer table splits into side-by-side blocks
MAX_COLUMNS = 3
BLOCK_GAP = {2: 20.0, 3: 16.0}
# Proportions of a block's width: number, car, direction, spaces. Used only for
# multi-column tables; a single-column table keeps COLUMNS exactly as printed
# sheets already have it, so nothing printed before stops scanning.
BLOCK_SHARES = (0.135, 0.255, 0.355, 0.255)

BOARD_CEILING = 628.0
BOARD_MAX = 280.0
BOARD_MIN = 180.0
BOARD_SHRINK_AFTER = 10  # rows that still allow a full-size board
GRID = 6

CODE_REGION = [38.0, 712.0, 300.0, 34.0]
# The QR code carries the puzzle itself. Its position also tells the scanner
# which way up the page was photographed, since a QR decodes either way round.
QR_RECT = [506.0, 686.0, 64.0, 64.0]
QR_QUIET = 4.0
TITLE_BASELINE = 722.0


def row_count(shortest_moves: int) -> int:
    """Answer rows to print: room to be inefficient, but still one page."""
    return max(MIN_ROWS, min(MAX_ROWS, int(shortest_moves) + 4))


def fits_on_one_page(shortest_moves: int) -> bool:
    """A sheet prints one row per move plus spares, and must stay one page."""
    return int(shortest_moves) + 4 <= MAX_ROWS


def mark_centers() -> dict[str, list[float]]:
    """Centers of the four black registration squares, used to rectify photos."""
    inset = MARK_INSET + MARK_SIZE / 2
    return {
        "bl": [inset, inset],
        "br": [PAGE_W - inset, inset],
        "tl": [inset, PAGE_H - inset],
        "tr": [PAGE_W - inset, PAGE_H - inset],
    }


def column_count(rows: int) -> int:
    """How many side-by-side answer blocks a sheet of `rows` rows needs."""
    rows = max(MIN_ROWS, min(MAX_ROWS, int(rows)))
    return max(1, min(MAX_COLUMNS, -(-rows // ROWS_PER_COLUMN)))


def block_widths(columns: int, width: float) -> tuple[float, list[float]]:
    """Width of one answer block and of its four cells."""
    if columns == 1:
        return width, list(COLUMNS)
    gap = BLOCK_GAP[columns]
    block = (width - gap * (columns - 1)) / columns
    return block, [block * share for share in BLOCK_SHARES]


def layout(rows: int) -> dict:
    """Resolve every drawable and scannable rectangle for a sheet of `rows` rows.

    Answer rows are numbered in reading order: down the first block, then the
    next. The scanner only walks ``answerRows``, so it never needs to know how
    many blocks a sheet was printed with.
    """
    rows = max(MIN_ROWS, min(MAX_ROWS, int(rows)))
    width = PAGE_W - 2 * MARGIN
    columns = column_count(rows)
    per_column = -(-rows // columns)
    block_w, cell_widths = block_widths(columns, width)
    gap = BLOCK_GAP[columns] if columns > 1 else 0.0

    # Vertical space depends on the tallest block, not the total row count.
    extra = max(0, per_column - BOARD_SHRINK_AFTER)
    board_size = max(BOARD_MIN, min(BOARD_MAX, BOARD_MAX - extra * 24.0))
    board_bottom = BOARD_CEILING - board_size
    board_caption = board_bottom - 12.0
    table_title = board_caption - 22.0

    row_h = max(ROW_H_MIN, min(ROW_H_MAX,
                               (table_title - 14.0 - TABLE_BOTTOM - HEADER_H) / per_column))
    table_h = HEADER_H + per_column * row_h

    team_width = TEAM_BOXES * TEAM_BOX + (TEAM_BOXES - 1) * TEAM_GAP
    team_x = PAGE_W - MARGIN - team_width
    team = [
        [team_x + i * (TEAM_BOX + TEAM_GAP), TEAM_Y, TEAM_BOX, TEAM_BOX]
        for i in range(TEAM_BOXES)
    ]

    blocks = []
    for block in range(columns):
        left = MARGIN + block * (block_w + gap)
        edges, x = [], left
        for cell_w in cell_widths:
            edges.append([x, cell_w])
            x += cell_w
        blocks.append({
            "x": left,
            "width": block_w,
            "edges": edges,
            "table": [left, TABLE_BOTTOM, block_w, table_h],
            "header": [left, TABLE_BOTTOM + per_column * row_h, block_w, HEADER_H],
        })

    answer_rows = []
    for index in range(rows):
        block = blocks[min(columns - 1, index // per_column)]
        within = index % per_column
        bottom = TABLE_BOTTOM + (per_column - within - 1) * row_h
        cells = {}
        for name, (cell_x, cell_w) in zip(("number", "car", "direction", "spaces"), block["edges"]):
            cells[name] = [cell_x, bottom, cell_w, row_h]
        answer_rows.append(cells)

    return {
        "page": [PAGE_W, PAGE_H],
        "rows": rows,
        "columns": columns,
        "rowsPerColumn": per_column,
        "rowHeight": row_h,
        "marks": mark_centers(),
        "markSize": MARK_SIZE,
        "code": list(CODE_REGION),
        "titleBaseline": TITLE_BASELINE,
        "team": team,
        "teamLabelBaseline": TEAM_Y + TEAM_BOX + 8.0,
        "blocks": blocks,
        "table": [MARGIN, TABLE_BOTTOM, width, table_h],
        "tableTitleBaseline": table_title,
        "answerRows": answer_rows,
        "board": [(PAGE_W - board_size) / 2, board_bottom, board_size, board_size],
        "boardCaptionBaseline": board_caption,
        "pointsBadge": [424.0, 712.0, 70.0, 28.0],
        "qr": list(QR_RECT),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(layout(MAX_ROWS), indent=2))
