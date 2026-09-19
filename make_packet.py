#!/usr/bin/env python3
"""Build a printable Gridlock Sprint packet: student PDF, answer key, and manifest.

The grading station reads the manifest, so keep the three files together.

    python3 make_packet.py --easy 4 --medium 4 --hard 2 --id GS-2026-R1
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import packet_pdf

APP_DIR = Path(__file__).resolve().parent
GENERATOR = APP_DIR / "generate_packet.js"
DEFAULT_ROOT = APP_DIR / "output" / "packets"


def generate_manifest(config: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        raise SystemExit(
            "Node.js is required to generate puzzles (it shares engine.js with the grader).\n"
            "Install it from https://nodejs.org and run this again."
        )
    process = subprocess.run(
        [node, str(GENERATOR), json.dumps(config)],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(APP_DIR),
    )
    if process.returncode != 0:
        raise SystemExit(f"Puzzle generation failed:\n{process.stderr.strip()}")
    return json.loads(process.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--easy", type=int, default=4, help="number of easy puzzles (3-6 moves)")
    parser.add_argument("--medium", type=int, default=4, help="number of medium puzzles (7-12 moves)")
    parser.add_argument("--hard", type=int, default=2, help="number of hard puzzles (13-20 moves)")
    parser.add_argument("--grandmaster", type=int, default=1,
                        help="number of grandmaster puzzles (21-40 moves)")
    parser.add_argument("--points-easy", type=int, default=2)
    parser.add_argument("--points-medium", type=int, default=4)
    parser.add_argument("--points-hard", type=int, default=8)
    parser.add_argument("--points-grandmaster", type=int, default=15)
    parser.add_argument("--seed", type=int, default=None, help="repeat a previous packet exactly")
    parser.add_argument("--id", dest="packet_id", default=None, help="packet code printed on every page")
    parser.add_argument("--title", default="Middle School Math Meet")
    parser.add_argument("--round", dest="round_name", default="Gridlock Sprint")
    parser.add_argument("--out", type=Path, default=DEFAULT_ROOT, help="folder that holds packet folders")
    parser.add_argument("--open", dest="open_pdfs", action="store_true",
                        help="open both PDFs when they are built, ready to print")
    args = parser.parse_args()

    if args.easy + args.medium + args.hard + args.grandmaster <= 0:
        raise SystemExit("Ask for at least one puzzle.")

    stamp = datetime.now()
    seed = args.seed if args.seed is not None else int(stamp.timestamp()) % 2147483647
    packet_id = args.packet_id or f"GS-{stamp:%Y%m%d}-{seed % 1000:03d}"

    config = {
        "seed": seed,
        "packetId": packet_id,
        "title": args.title,
        "round": args.round_name,
        "counts": {
            "easy": args.easy,
            "medium": args.medium,
            "hard": args.hard,
            "grandmaster": args.grandmaster,
        },
        "points": {
            "easy": args.points_easy,
            "medium": args.points_medium,
            "hard": args.points_hard,
            "grandmaster": args.points_grandmaster,
        },
    }

    total = args.easy + args.medium + args.hard + args.grandmaster
    print(f"Generating {total} puzzles… (grandmaster boards need a long search)")
    manifest = generate_manifest(config)
    manifest["createdAt"] = stamp.isoformat(timespec="seconds")

    folder = args.out / packet_id
    packet_path = folder / "packet.pdf"
    key_path = folder / "key.pdf"
    packet_pdf.render_packet(manifest, packet_path, key_path)
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))

    for warning in manifest.get("warnings", []):
        print(f"  note: {warning}", file=sys.stderr)
    print(f"\nPacket {packet_id}: {len(manifest['puzzles'])} puzzles, {manifest['totalPoints']} points total")
    for puzzle in manifest["puzzles"]:
        print(f"  page {puzzle['index']:>2}  {puzzle['code']}  {puzzle['level']:<12} {puzzle['points']:>2} pts  "
              f"shortest {puzzle['shortestMoves']:>2} moves  {puzzle['sheetRows']:>2} rows")
    print(f"\n  student packet  {packet_path}")
    print(f"  answer key      {key_path}")
    print(f"  manifest        {folder / 'manifest.json'}")
    print("\nPrint packet.pdf at 100% scale (no 'shrink to fit') so the corner marks survive.")
    print("Keep manifest.json beside the PDFs: the grading station reads it.")
    if args.open_pdfs:
        subprocess.run(["/usr/bin/open", str(packet_path), str(key_path)], check=False)


if __name__ == "__main__":
    main()
