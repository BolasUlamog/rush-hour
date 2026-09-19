#!/usr/bin/env python3
"""Classify the characters written in an answer cell.

A cell holds one or two block characters drawn from a tiny alphabet, which a
text-line recognizer handles badly and a character classifier handles well. This
runs models/glyphs.onnx (a small CNN trained on EMNIST by train_glyph_model.py)
through onnxruntime, restricted to the characters a given column can hold.

Restricting matters: on EMNIST's own test set the same network is 89% accurate
across all 47 classes but 99% on L/R/U/D and 99.8% on the digits 1-5.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

import glyph_preprocess

MODEL = Path(__file__).resolve().parent / "models" / "glyphs.onnx"
GAP_RATIO = 0.13          # blank columns wider than this split two characters
MIN_GLYPH_RATIO = 0.06    # narrower runs of ink are specks, not characters


class GlyphReader:
    """Lazily loaded classifier; one instance is reused for a whole sheet."""

    def __init__(self, model_path: Path = MODEL):
        self.model_path = model_path
        self._session = None
        self._labels: list[str] = []

    @property
    def available(self) -> bool:
        return self.model_path.is_file()

    def _load(self):
        if self._session is not None:
            return
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        options.intra_op_num_threads = 2
        self._session = onnxruntime.InferenceSession(
            str(self.model_path), options, providers=["CPUExecutionProvider"]
        )
        meta = json.loads(self.model_path.with_suffix(".labels.json").read_text())
        self._labels = meta["labels"]

    def allowed(self, alphabet: str) -> list[tuple[int, str]]:
        """Label indices this column may produce, paired with the character to report.

        EMNIST keeps a separate class for the eleven lowercase letters whose shape
        differs from the capital, so a lowercase 'd' is folded back onto 'D'.
        """
        self._load()
        picks: list[tuple[int, str]] = []
        for character in dict.fromkeys(alphabet.upper()):
            for candidate in (character, character.lower()):
                if candidate in self._labels:
                    picks.append((self._labels.index(candidate), character))
        return picks

    def classify_many(self, crops: list[Image.Image], alphabet: str, top: int = 3) -> list[list[tuple[str, float]]]:
        """Rank the allowed characters for each crop, with probabilities."""
        if not crops:
            return []
        self._load()
        allowed = self.allowed(alphabet)
        if not allowed:
            return [[] for _ in crops]

        size = glyph_preprocess.SIZE
        tensor = np.stack([
            np.frombuffer(glyph_preprocess.prepare(crop).tobytes(), dtype=np.uint8).reshape(size, size)
            for crop in crops
        ]).astype(np.float32)[:, None] / 255.0
        logits = self._session.run(["logits"], {"glyph": tensor})[0]

        results = []
        for row in logits:
            scores: dict[str, float] = {}
            for index, character in allowed:
                scores[character] = max(scores.get(character, -math.inf), float(row[index]))
            highest = max(scores.values())
            weights = {character: math.exp(score - highest) for character, score in scores.items()}
            total = sum(weights.values())
            ranked = sorted(((character, weight / total) for character, weight in weights.items()),
                            key=lambda item: -item[1])
            results.append(ranked[:top])
        return results

    def classify(self, crop: Image.Image, alphabet: str, top: int = 3) -> list[tuple[str, float]]:
        return self.classify_many([crop], alphabet, top)[0]

    def read_cell(self, crop: Image.Image, single: str, multi: str = "",
                  max_glyphs: int = 6) -> list[tuple[str, float]]:
        """Read a whole cell, whether it holds one character or a short word.

        `single` is the alphabet for a lone character (e.g. "LRUD"); `multi` is
        the wider alphabet to use per character when the cell holds a word
        (e.g. every letter, so LEFT can be spelled out).
        """
        pieces = segment(crop)
        if not pieces:
            return []
        if len(pieces) == 1:
            return self.classify(pieces[0], single)
        if not multi:
            return self.classify(pieces[0], single)
        ranked = self.classify_many(pieces[:max_glyphs], multi, top=1)
        text = "".join(options[0][0] for options in ranked if options)
        score = min((options[0][1] for options in ranked if options), default=0.0)
        return [(text, score)]


def segment(crop: Image.Image) -> list[Image.Image]:
    """Split a cell into its separate characters using blank pixel columns."""
    gray = crop.convert("L")
    histogram = gray.histogram()
    total = sum(histogram) or 1
    running = 0
    paper = 255
    for value, count in enumerate(histogram):
        running += count
        if running >= total * 0.6:
            paper = value
            break
    cutoff = max(40, int(paper * 0.72))
    mask = gray.point(lambda value: 1 if value <= cutoff else 0)
    data = mask.tobytes()
    width, height = mask.size
    columns = [sum(data[y * width + x] for y in range(height)) for x in range(width)]

    gap = max(2, int(height * GAP_RATIO))
    minimum = max(2, int(height * MIN_GLYPH_RATIO))
    runs: list[list[int]] = []
    blank = 0
    for x, amount in enumerate(columns):
        if amount:
            if runs and blank <= gap:
                runs[-1][1] = x
            else:
                runs.append([x, x])
            blank = 0
        else:
            blank += 1
    pieces = []
    for left, right in runs:
        if right - left + 1 < minimum:
            continue
        pad = max(1, height // 12)
        pieces.append(crop.crop((max(0, left - pad), 0, min(width, right + 1 + pad), height)))
    return pieces or [crop]
