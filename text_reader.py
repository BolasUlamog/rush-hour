#!/usr/bin/env python3
"""Apple's on-device text recognizer, when it is available.

This is the *second* of the two readers a sheet goes through. The character
model in glyph_reader.py does the main work; this one is good at directions
written out as words and acts as an independent opinion, which is what catches
the model's near-ties.

It only exists on a Mac: it shells out to a small Swift program built against the
Vision framework. Everywhere else — a Linux server, for instance — `available`
is False and the scanner runs on the character model alone, flagging anything it
is not confident about instead of having a second reader to compare against.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

APP_DIR = Path(__file__).resolve().parent
SOURCE = APP_DIR / "handwriting_ocr.swift"
BINARY = Path(tempfile.gettempdir()) / "gridlock-handwriting-ocr"
SWIFTC = "/usr/bin/swiftc"


class TextReader:
    """Lazily built; one instance is reused for a whole grading session."""

    def __init__(self, source: Path = SOURCE, binary: Path = BINARY):
        self.source = source
        self.binary = binary
        self._ready: bool | None = None
        self.reason = ""

    @property
    def available(self) -> bool:
        if self._ready is None:
            self._ready = self._prepare()
        return self._ready

    def _prepare(self) -> bool:
        if platform.system() != "Darwin":
            self.reason = "Apple's text recognizer needs macOS."
            return False
        if not self.source.is_file():
            self.reason = f"{self.source.name} is missing."
            return False
        if self.binary.exists() and self.binary.stat().st_mtime >= self.source.stat().st_mtime:
            return True
        if not shutil.which(SWIFTC) and not Path(SWIFTC).exists():
            self.reason = "swiftc is not installed, so the text recognizer cannot be built."
            return False
        environment = os.environ.copy()
        cache = Path(tempfile.gettempdir())
        environment["SWIFT_MODULECACHE_PATH"] = str(cache / "gridlock-swift-cache")
        environment["CLANG_MODULE_CACHE_PATH"] = str(cache / "gridlock-clang-cache")
        try:
            process = subprocess.run(
                [SWIFTC, str(self.source), "-o", str(self.binary)],
                capture_output=True, text=True, timeout=180, env=environment,
            )
        except (OSError, subprocess.SubprocessError) as error:
            self.reason = f"the text recognizer could not be built: {error}"
            return False
        if process.returncode != 0:
            self.reason = process.stderr.strip() or "the text recognizer could not be built."
            return False
        return True

    def read(self, image: Image.Image, regions: list[dict]) -> list[dict]:
        """Recognize the given regions, returning one entry per region."""
        if not self.available:
            return []
        with tempfile.TemporaryDirectory() as folder:
            image_path = Path(folder) / "page.png"
            image.save(image_path)
            regions_path = Path(folder) / "regions.json"
            regions_path.write_text(json.dumps(regions))
            process = subprocess.run(
                [str(self.binary), str(image_path), "--regions", str(regions_path)],
                capture_output=True, text=True, timeout=120,
            )
        if process.returncode != 0:
            return []
        try:
            return json.loads(process.stdout or "[]")
        except json.JSONDecodeError:
            return []
