#!/usr/bin/env python3
"""Train the answer-cell character classifier and export it as ONNX.

Development only. The grading station never needs PyTorch: it loads the exported
models/glyphs.onnx with onnxruntime.

    python3.13 -m venv .venv-train
    .venv-train/bin/pip install -r requirements-train.txt
    .venv-train/bin/python train_glyph_model.py --data ~/emnist

EMNIST's gzipped idx files are expected in --data. Get them from
https://www.nist.gov/itl/products-and-services/emnist-dataset (gzip.zip), or the
per-file mirror at https://github.com/aurelienduarte/emnist/tree/master/gzip
(those are git-lfs pointers on raw.githubusercontent.com; fetch them from
media.githubusercontent.com/media/... instead).

Why the augmentation looks the way it does: glyph_preprocess.prepare() picks a
black point off the cell's own histogram but never a white point, so a faintly
pencilled character arrives at the model dim -- light pencil on white paper
normalizes to a glyph peaking around 80/255. Every EMNIST glyph peaks at 255, so
a model trained on EMNIST as-shipped has never seen the case it fails on. Dimming,
blurring and thinning the training glyphs is therefore not generic regularization;
it is the distribution the grading station actually feeds it.
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
    """Small enough to run per cell on a laptop CPU, big enough for 47 classes.

    Three stages rather than two: the confusions left over from the two-stage
    version (2 against 1, J against L against F, B against D) are all pairs that
    differ in one small piece of the glyph, and the third stage is what gives the
    head a 7x7 field of view over the whole character instead of 14x14 patches.
    """

    def __init__(self, classes: int = len(LABELS), width: int = 40):
        super().__init__()

        def stage(inputs: int, outputs: int, drop: float) -> nn.Sequential:
            return nn.Sequential(
                nn.Conv2d(inputs, outputs, 3, padding=1), nn.BatchNorm2d(outputs), nn.ReLU(),
                nn.Conv2d(outputs, outputs, 3, padding=1), nn.BatchNorm2d(outputs), nn.ReLU(),
                nn.MaxPool2d(2), nn.Dropout(drop),
            )

        self.block1 = stage(1, width, 0.1)                 # 28 -> 14
        self.block2 = stage(width, width * 2, 0.2)         # 14 -> 7
        self.block3 = stage(width * 2, width * 4, 0.3)     # 7  -> 3
        self.head = nn.Sequential(
            nn.Flatten(), nn.Linear(width * 4 * 3 * 3, 256), nn.ReLU(), nn.Dropout(0.4),
            nn.Linear(256, classes),
        )

    def forward(self, x):
        return self.head(self.block3(self.block2(self.block1(x))))


def spread(count: int, low: float, high: float, generator: torch.Generator,
           device: torch.device, image_shaped: bool = False) -> torch.Tensor:
    """One uniform value per glyph in the batch."""
    shape = (count, 1, 1, 1) if image_shaped else (count,)
    return torch.rand(shape, generator=generator, device=device) * (high - low) + low


def jitter(batch: torch.Tensor, generator: torch.Generator) -> torch.Tensor:
    """Rotate, scale, shear and shift the way a photographed cell is jittered."""
    count = batch.shape[0]
    device = batch.device

    angle = spread(count, -0.22, 0.22, generator, device)      # about +/- 12 degrees
    scale = spread(count, 0.86, 1.14, generator, device)
    shear = spread(count, -0.16, 0.16, generator, device)
    shift_x = spread(count, -0.12, 0.12, generator, device)
    shift_y = spread(count, -0.12, 0.12, generator, device)

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


BLUR = torch.tensor([[1.0, 2.0, 1.0], [2.0, 4.0, 2.0], [1.0, 2.0, 1.0]]) / 16.0


def fade(batch: torch.Tensor, generator: torch.Generator, strength: float = 1.0) -> torch.Tensor:
    """Make glyphs look pencilled rather than printed: dim, soft, thin, broken.

    Each effect runs at a per-glyph random amount whose low end is a no-op, so the
    batch covers everything from a crisp marker stroke to the faintest pencil the
    scanner still finds ink in.
    """
    count = batch.shape[0]
    device = batch.device
    faded = batch

    # Thin or thicken the stroke. Grayscale erosion is a min filter, which is a
    # max filter on the negative; a thinned stroke is what faint pencil leaves.
    thinner = -F.max_pool2d(-faded, 3, 1, 1)
    thicker = F.max_pool2d(faded, 3, 1, 1)
    amount = spread(count, -1.0 * strength, 0.5 * strength, generator, device, image_shaped=True)
    faded = torch.where(amount < 0, faded + (thinner - faded) * -amount,
                        faded + (thicker - faded) * amount)

    # Soften: camera focus, page curl, and prepare()'s own Gaussian.
    kernel = BLUR.to(device).view(1, 1, 3, 3)
    blurred = F.conv2d(F.pad(faded, (1, 1, 1, 1), mode="constant"), kernel)
    faded = faded + (blurred - faded) * spread(count, 0.0, 1.0 * strength, generator, device,
                                               image_shaped=True)

    # Dim. prepare() pins the black point to the paper but leaves the white point
    # alone, so light pencil reaches the model at a fraction of full brightness.
    faded = faded * spread(count, 1.0 - 0.7 * strength, 1.0, generator, device, image_shaped=True)
    faded = faded.clamp(min=1e-6) ** spread(count, 1.0, 1.0 + 0.8 * strength, generator, device,
                                            image_shaped=True)

    # Break the stroke: a dry pencil skips. The gaps are thin and irregular and
    # follow the grain of the paper, so this is a smooth low-frequency field that
    # dims patches of the glyph -- not a clean rectangular hole, which would teach
    # the model to invent whatever shape is missing.
    coarse = torch.rand((count, 1, 7, 7), generator=generator, device=device)
    grain = F.interpolate(coarse, size=batch.shape[-2:], mode="bilinear", align_corners=False)
    depth = spread(count, 0.0, 0.9 * strength, generator, device, image_shaped=True)
    faded = faded * (1.0 - depth * (1.0 - grain))

    # Paper grain, lifted along with the ink by the contrast stretch.
    noise = torch.randn(batch.shape, generator=generator, device=device)
    faded = faded + noise * spread(count, 0.0, 0.06 * strength, generator, device, image_shaped=True)
    return faded.clamp(0.0, 1.0)


def augment(batch: torch.Tensor, generator: torch.Generator) -> torch.Tensor:
    return fade(jitter(batch, generator), generator)


class WeightAverage:
    """Exponential moving average of the weights; the average is what ships.

    Late in a one-cycle schedule the weights still wander between batches, and the
    averaged copy is reliably a fraction of a percent better than whichever step
    training happened to stop on.
    """

    def __init__(self, model: nn.Module, decay: float = 0.999):
        self.decay = decay
        self.steps = 0
        self.shadow = {name: value.detach().clone()
                       for name, value in model.state_dict().items()}

    def update(self, model: nn.Module) -> None:
        # Ramp the decay in, or the first few thousand steps of the average are
        # mostly the random initialization and the epoch numbers mean nothing.
        self.steps += 1
        decay = min(self.decay, (1.0 + self.steps) / (10.0 + self.steps))
        for name, value in model.state_dict().items():
            saved = self.shadow[name]
            if saved.dtype.is_floating_point:
                saved.mul_(decay).add_(value.detach(), alpha=1.0 - decay)
            else:
                saved.copy_(value)          # BatchNorm's batch counter

    def into(self, model: nn.Module) -> nn.Module:
        model.load_state_dict(self.shadow)
        return model


# The sheet never asks for a free-form character: each column has its own tiny
# alphabet, so this is the accuracy that actually decides whether a row reads.
COLUMN_ALPHABETS = {
    "direction (L R U D)": "LRUD",
    "spaces (1-5)": "12345",
    "car label (X A-N)": "XABCDEFGHJKLMN",
    "team id (A-Z 0-9)": "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
}

# The confusions that survived the previous model, all of them on faint strokes.
WATCHED_PAIRS = (("2", "1"), ("J", "L"), ("L", "F"), ("J", "F"), ("B", "D"))


def folded(alphabet: str) -> tuple[list[int], list[str]]:
    """Label indices a column may produce, and the character each one reports.

    Mirrors glyph_reader.allowed(). EMNIST keeps separate classes for the eleven
    lowercase letters shaped differently from their capital, and the reader folds
    those back onto the capital, so scoring a capital F landing in class 'f' as an
    error would measure a mistake the grading station never makes.
    """
    indices, reported = [], []
    for character in dict.fromkeys(alphabet.upper()):
        for candidate in (character, character.lower()):
            if candidate in LABELS:
                indices.append(LABELS.index(candidate))
                reported.append(character)
    return indices, reported


def restricted(logits: torch.Tensor, labels: np.ndarray, alphabet: str) -> tuple[float, int]:
    """Accuracy once the choice is narrowed to what a given column can hold."""
    indices, reported = folded(alphabet)
    keep = np.isin(labels, indices)
    if not keep.any():
        return float("nan"), 0
    chosen = logits[torch.from_numpy(keep).to(logits.device)][:, indices].argmax(1).cpu().numpy()
    said = np.array(reported)[chosen]
    truth = np.array([reported[indices.index(int(value))] for value in labels[keep]])
    return float((said == truth).mean()), int(keep.sum())


def all_logits(model: nn.Module, images: torch.Tensor) -> torch.Tensor:
    with torch.no_grad():
        return torch.cat([model(images[start:start + 1024])
                          for start in range(0, len(images), 1024)])


def report(model: nn.Module, images: torch.Tensor, labels: np.ndarray, heading: str) -> float:
    """Accuracy once the choice is narrowed to what a given column can hold."""
    logits = all_logits(model, images)
    whole = (logits.argmax(1).cpu().numpy() == labels).mean()
    print(f"\n{heading}: {whole:.4f} across all {len(LABELS)} classes")
    for name, alphabet in COLUMN_ALPHABETS.items():
        hits, count = restricted(logits, labels, alphabet)
        print(f"  {name:<36} {hits:.4f}  ({count:,} glyphs)")

    print("  pairs that used to confuse it, one against the other:")
    for first, second in WATCHED_PAIRS:
        hits, count = restricted(logits, labels, first + second)
        print(f"    {first} vs {second}   {hits:.4f}  ({count:,} glyphs)")
    return float(whole)


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
        dynamo=False,
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
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch", type=int, default=256)
    parser.add_argument("--width", type=int, default=40, help="channels in the first stage")
    parser.add_argument("--out", type=Path, default=Path("models/glyphs.onnx"))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--export-only", action="store_true",
                        help="re-export the saved checkpoint without training again")
    arguments = parser.parse_args()

    torch.manual_seed(arguments.seed)
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

    test_x, test_y = load_split(arguments.data, "test")
    test_images = torch.from_numpy(test_x).float().div_(255).unsqueeze(1).to(device)
    # A second, dimmed copy of the test set. Clean EMNIST accuracy says nothing
    # about the photographs this model is for, so both numbers get reported.
    faint_generator = torch.Generator(device=device).manual_seed(1234)
    faint_images = fade(test_images, faint_generator, strength=1.0)

    if arguments.export_only:
        model = GlyphNet(width=arguments.width)
        model.load_state_dict(torch.load(arguments.out.with_suffix(".pt"), map_location="cpu"))
        model = model.eval().to(device)
        report(model, test_images, test_y, "clean EMNIST test set")
        report(model, faint_images, test_y, "the same glyphs faintly pencilled")
        export(model, arguments.out)
        return

    print(f"training on {device}")
    train_x, train_y = load_split(arguments.data, "train")
    print(f"{len(train_x):,} training glyphs, {len(test_x):,} test glyphs, {len(LABELS)} classes")

    train_images = torch.from_numpy(train_x).float().div_(255).unsqueeze(1)
    train_labels = torch.from_numpy(train_y)
    test_labels = torch.from_numpy(test_y).to(device)

    model = GlyphNet(width=arguments.width).to(device)
    print(f"{sum(p.numel() for p in model.parameters()):,} parameters")
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=5e-4)
    steps = (len(train_images) // arguments.batch) * arguments.epochs
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=3e-3, total_steps=steps)
    generator = torch.Generator(device=device).manual_seed(arguments.seed)
    average = WeightAverage(model)
    spare = GlyphNet(width=arguments.width).to(device)

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
            average.update(model)
            running += loss.item() * len(index)
            correct += (logits.argmax(1) == labels).sum().item()
            seen += len(index)

        model.eval()
        averaged = average.into(spare).eval()

        def accuracy(net: nn.Module, images: torch.Tensor) -> float:
            with torch.no_grad():
                return sum((net(images[at:at + 1024]).argmax(1)
                            == test_labels[at:at + 1024]).sum().item()
                           for at in range(0, len(images), 1024)) / len(images)

        print(f"epoch {epoch:>2}  loss {running / seen:.4f}  train {correct / seen:.4f}"
              f"  test {accuracy(model, test_images):.4f}"
              f"  averaged {accuracy(averaged, test_images):.4f}"
              f"  averaged-faint {accuracy(averaged, faint_images):.4f}")

    shipped = average.into(spare).eval()
    checkpoint = arguments.out.with_suffix(".pt")
    torch.save({name: value.cpu() for name, value in shipped.state_dict().items()}, checkpoint)
    print(f"\nwrote {checkpoint}")
    report(shipped, test_images, test_y, "clean EMNIST test set")
    report(shipped, faint_images, test_y, "the same glyphs faintly pencilled")
    export(shipped, arguments.out)


if __name__ == "__main__":
    main()
