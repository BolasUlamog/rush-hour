#!/usr/bin/env python3
"""Train the answer-cell character classifier and export it as ONNX.

Development only. The grading station never needs PyTorch: it loads the exported
models/glyphs.onnx with onnxruntime.

    python3 -m venv .venv-train
    .venv-train/bin/pip install -r requirements-train.txt
    .venv-train/bin/python train_glyph_model.py --data ~/emnist

EMNIST's gzipped idx files are expected in --data. Get them from
https://www.nist.gov/itl/products-and-services/emnist-dataset (gzip.zip), or the
per-file mirror at https://github.com/aurelienduarte/emnist/tree/master/gzip
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import numpy as np
import onnx
import torch
import torch.nn as nn
import torch.nn.functional as F

# EMNIST Balanced: 10 digits, 26 uppercase letters, and the 11 lowercase letters
# whose shape differs from their capital. The rest are merged into the capitals,
# which suits a sheet that asks for capitals but may well get lowercase.
LABELS = [*"0123456789", *"ABCDEFGHIJKLMNOPQRSTUVWXYZ", *"abdefghnqrt"]


def read_idx(path: Path) -> np.ndarray:
    with gzip.open(path, "rb") as handle:
        raw = handle.read()
    dimensions = raw[3]
    shape = [int.from_bytes(raw[4 + 4 * i: 8 + 4 * i], "big") for i in range(dimensions)]
    return np.frombuffer(raw[4 + 4 * dimensions:], dtype=np.uint8).reshape(shape)


def load_split(folder: Path, split: str) -> tuple[np.ndarray, np.ndarray]:
    images = read_idx(folder / f"emnist-balanced-{split}-images-idx3-ubyte.gz")
    labels = read_idx(folder / f"emnist-balanced-{split}-labels-idx1-ubyte.gz")
    # EMNIST stores its images transposed; upright is the transpose.
    return np.ascontiguousarray(images.transpose(0, 2, 1)), labels.astype(np.int64)


class GlyphNet(nn.Module):
    """Small enough to run per cell on a laptop CPU, big enough for 47 classes."""

    def __init__(self, classes: int = len(LABELS)):
        super().__init__()
        self.block1 = nn.Sequential(
            nn.Conv2d(1, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(),
            nn.Conv2d(32, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(),
            nn.MaxPool2d(2), nn.Dropout(0.25),
        )
        self.block2 = nn.Sequential(
            nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
            nn.Conv2d(64, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
            nn.MaxPool2d(2), nn.Dropout(0.25),
        )
        self.head = nn.Sequential(
            nn.Flatten(), nn.Linear(64 * 7 * 7, 256), nn.ReLU(), nn.Dropout(0.4),
            nn.Linear(256, classes),
        )

    def forward(self, x):
        return self.head(self.block2(self.block1(x)))


def augment(batch: torch.Tensor, generator: torch.Generator) -> torch.Tensor:
    """Jitter each glyph the way a photographed, rectified cell is jittered."""
    count = batch.shape[0]
    device = batch.device

    def uniform(low, high):
        return torch.rand(count, generator=generator, device=device) * (high - low) + low

    angle = uniform(-0.22, 0.22)          # about +/- 12 degrees
    scale = uniform(0.86, 1.14)
    shear = uniform(-0.16, 0.16)
    shift_x = uniform(-0.12, 0.12)
    shift_y = uniform(-0.12, 0.12)

    cos, sin = torch.cos(angle) / scale, torch.sin(angle) / scale
    theta = torch.zeros(count, 2, 3, device=device)
    theta[:, 0, 0] = cos
    theta[:, 0, 1] = -sin + shear
    theta[:, 0, 2] = shift_x
    theta[:, 1, 0] = sin
    theta[:, 1, 1] = cos
    theta[:, 1, 2] = shift_y
    grid = F.affine_grid(theta, batch.shape, align_corners=False)
    return F.grid_sample(batch, grid, align_corners=False, padding_mode="zeros")


# The sheet never asks for a free-form character: each column has its own tiny
# alphabet, so this is the accuracy that actually decides whether a row reads.
COLUMN_ALPHABETS = {
    "direction (L R U D)": "LRUD",
    "spaces (1-5)": "12345",
    "car label (X A-N)": "XABCDEFGHJKLMN",
    "team id (A-Z 0-9)": "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
}


def report_restricted(model: nn.Module, images: np.ndarray, labels: np.ndarray) -> None:
    """Accuracy once the choice is narrowed to what a given column can hold."""
    tensor = torch.from_numpy(images).float().div_(255).unsqueeze(1)
    with torch.no_grad():
        logits = torch.cat([model(tensor[start:start + 1024]) for start in range(0, len(tensor), 1024)])
    print("\naccuracy restricted to each column's alphabet:")
    for name, alphabet in COLUMN_ALPHABETS.items():
        allowed = sorted({LABELS.index(character) for character in alphabet if character in LABELS}
                         | {LABELS.index(character.lower()) for character in alphabet
                            if character.lower() in LABELS})
        keep = np.isin(labels, allowed)
        if not keep.any():
            continue
        subset = logits[torch.from_numpy(keep)][:, allowed]
        truth = torch.tensor([allowed.index(int(value)) for value in labels[keep]])
        hits = (subset.argmax(1) == truth).float().mean().item()
        print(f"  {name:<36} {hits:.4f}  ({int(keep.sum()):,} glyphs)")


def export(model: nn.Module, out: Path) -> None:
    """Write one self-contained .onnx file, weights included."""
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model.eval().to("cpu"),
        (torch.zeros(1, 1, 28, 28),),
        str(out),
        input_names=["glyph"],
        output_names=["logits"],
        dynamic_axes={"glyph": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=17,
    )
    # The exporter splits weights into a sidecar .data file; fold them back in so
    # the grading station only has one file to carry.
    sidecar = Path(f"{out}.data")
    graph = onnx.load(str(out))
    onnx.save_model(graph, str(out), save_as_external_data=False)
    sidecar.unlink(missing_ok=True)
    labels_path = out.with_suffix(".labels.json")
    labels_path.write_text(json.dumps({"labels": LABELS, "size": 28}, indent=2))
    print(f"\nwrote {out} ({out.stat().st_size / 1024:.0f} KB)")
    print(f"wrote {labels_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True, help="folder holding the EMNIST gzip idx files")
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch", type=int, default=256)
    parser.add_argument("--out", type=Path, default=Path("models/glyphs.onnx"))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--export-only", action="store_true",
                        help="re-export the saved checkpoint without training again")
    arguments = parser.parse_args()

    torch.manual_seed(arguments.seed)
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

    if arguments.export_only:
        model = GlyphNet()
        model.load_state_dict(torch.load(arguments.out.with_suffix(".pt"), map_location="cpu"))
        model.eval()
        test_x, test_y = load_split(arguments.data, "test")
        report_restricted(model, test_x, test_y)
        export(model, arguments.out)
        return

    print(f"training on {device}")
    train_x, train_y = load_split(arguments.data, "train")
    test_x, test_y = load_split(arguments.data, "test")
    print(f"{len(train_x):,} training glyphs, {len(test_x):,} test glyphs, {len(LABELS)} classes")

    train_images = torch.from_numpy(train_x).float().div_(255).unsqueeze(1)
    train_labels = torch.from_numpy(train_y)
    test_images = torch.from_numpy(test_x).float().div_(255).unsqueeze(1).to(device)
    test_labels = torch.from_numpy(test_y).to(device)

    model = GlyphNet().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    steps = (len(train_images) // arguments.batch) * arguments.epochs
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=3e-3, total_steps=steps)
    generator = torch.Generator(device=device).manual_seed(arguments.seed)

    step = 0
    for epoch in range(1, arguments.epochs + 1):
        model.train()
        order = torch.randperm(len(train_images))
        running = correct = seen = 0
        for start in range(0, len(order) - arguments.batch + 1, arguments.batch):
            index = order[start:start + arguments.batch]
            images = train_images[index].to(device)
            labels = train_labels[index].to(device)
            images = augment(images, generator)
            optimizer.zero_grad(set_to_none=True)
            logits = model(images)
            loss = F.cross_entropy(logits, labels, label_smoothing=0.05)
            loss.backward()
            optimizer.step()
            if step < steps - 1:
                schedule.step()
            step += 1
            running += loss.item() * len(index)
            correct += (logits.argmax(1) == labels).sum().item()
            seen += len(index)

        model.eval()
        with torch.no_grad():
            hits = 0
            for start in range(0, len(test_images), 1024):
                chunk = test_images[start:start + 1024]
                hits += (model(chunk).argmax(1) == test_labels[start:start + 1024]).sum().item()
        print(f"epoch {epoch:>2}  loss {running / seen:.4f}  train {correct / seen:.4f}  test {hits / len(test_images):.4f}")

    checkpoint = arguments.out.with_suffix(".pt")
    torch.save(model.eval().to("cpu").state_dict(), checkpoint)
    print(f"\nwrote {checkpoint}")
    report_restricted(model, test_x, test_y)
    export(model, arguments.out)


if __name__ == "__main__":
    main()
