#!/usr/bin/env python3
"""Read a line of handwriting with PP-OCR's recognition model.

This is the cross-platform second reader. Apple's Vision framework fills that
role on a Mac and cannot run anywhere else, which cost real accuracy on the
deployed server: the character model alone misread 4 of 13 rows on a test photo
that the pair read perfectly.

RapidOCR packages these PP-OCR models nicely, but it depends on OpenCV, and
OpenCV alone is 119 MB installed — more than the remaining room inside Vercel's
250 MB function limit. Its detection stage is what needs OpenCV, and this app
does not need detection at all: sheet_scan composes the digest itself, so it
already knows exactly where every line sits. So only the recognition model is
used here, driven by the onnxruntime that the character model already needs.

The model carries its own character dictionary in its ONNX metadata, so
models/ppocr_rec.onnx is self-contained.

PP-OCR models are from PaddlePaddle/PaddleOCR, Apache 2.0 licensed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

MODEL = Path(__file__).resolve().parent / "models" / "ppocr_rec.onnx"
HEIGHT = 48                 # the height PP-OCR's recognizer expects
MIN_WIDTH = 16
MAX_WIDTH = 1600


class LineReader:
    """Lazily loaded; one instance is reused for a whole grading session."""

    def __init__(self, model_path: Path = MODEL):
        self.model_path = model_path
        self._session = None
        self._labels: list[str] = []
        self.reason = ""

    @property
    def available(self) -> bool:
        if not self.model_path.is_file():
            self.reason = f"{self.model_path.name} is missing."
            return False
        try:
            self._load()
        except Exception as error:                       # pragma: no cover
            self.reason = f"the line reader could not start: {error}"
            return False
        return True

    def _load(self) -> None:
        if self._session is not None:
            return
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        options.intra_op_num_threads = 2
        self._session = onnxruntime.InferenceSession(
            str(self.model_path), options, providers=["CPUExecutionProvider"]
        )
        dictionary = self._session.get_modelmeta().custom_metadata_map.get("character", "")
        # PP-OCR's decoder puts the CTC blank first and a space last.
        self._labels = ["", *dictionary.split("\n"), " "]

    def read(self, image: Image.Image) -> tuple[str, float]:
        """Recognize one line, returning the text and a mean character confidence."""
        if not self.available:
            return "", 0.0
        self._load()
        picture = image.convert("RGB")
        width = round(picture.width * HEIGHT / max(1, picture.height))
        width = max(MIN_WIDTH, min(MAX_WIDTH, width))
        picture = picture.resize((width, HEIGHT), Image.BILINEAR)

        array = np.asarray(picture, dtype=np.float32) / 255.0
        array = (array - 0.5) / 0.5
        tensor = array.transpose(2, 0, 1)[None]
        # The model already emits probabilities, so there is no softmax here.
        probabilities = self._session.run(None, {"x": tensor})[0][0]

        best = probabilities.argmax(axis=1)
        characters: list[str] = []
        scores: list[float] = []
        previous = -1
        for step, index in enumerate(best):
            if index != previous and index != 0:
                characters.append(self._labels[index] if index < len(self._labels) else "")
                scores.append(float(probabilities[step, index]))
            previous = index
        return "".join(characters).strip(), (float(np.mean(scores)) if scores else 0.0)
