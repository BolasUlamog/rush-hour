#!/usr/bin/env python3
"""Turn a cropped answer cell into an EMNIST-shaped 28x28 glyph.

Training and inference must prepare images identically, and the EMNIST authors
describe a specific procedure, so it lives here and is imported by both
train_glyph_model.py and glyph_reader.py.

EMNIST's conversion: take the region of interest, apply a Gaussian filter,
centre it in a square, pad by 2 pixels a side, and downsample to 28x28. The
result is white ink on a black ground.
"""

from __future__ import annotations

from PIL import Image, ImageFilter, ImageOps

SIZE = 28
PAD = 2


def prepare(crop: Image.Image, ink_is_dark: bool = True) -> Image.Image:
    """Normalize one handwritten character the way EMNIST was built."""
    glyph = crop.convert("L")
    if ink_is_dark:
        glyph = ImageOps.invert(glyph)          # now white ink on black

    # Lift the ink off the paper: anything near the darkest paper value is ground.
    histogram = glyph.histogram()
    total = sum(histogram)
    floor = 0
    running = 0
    for value, count in enumerate(histogram):
        running += count
        if running >= total * 0.55:             # most of a cell is background
            floor = value
            break
    span = max(1, 255 - floor)
    glyph = glyph.point(lambda value: 0 if value <= floor else min(255, int((value - floor) * 255 / span)))

    box = glyph.point(lambda value: 255 if value > 40 else 0).getbbox()
    if box is None:
        return Image.new("L", (SIZE, SIZE), 0)
    glyph = glyph.crop(box)

    glyph = glyph.filter(ImageFilter.GaussianBlur(max(0.5, min(glyph.size) / 28)))

    side = max(glyph.size)
    square = Image.new("L", (side, side), 0)
    square.paste(glyph, ((side - glyph.width) // 2, (side - glyph.height) // 2))

    padded = Image.new("L", (side + PAD * 2 * side // SIZE + 2, side + PAD * 2 * side // SIZE + 2), 0)
    padded.paste(square, ((padded.width - side) // 2, (padded.height - side) // 2))
    return padded.resize((SIZE, SIZE), Image.BICUBIC)


def to_vector(glyph: Image.Image) -> list[float]:
    return [value / 255.0 for value in glyph.tobytes()]
