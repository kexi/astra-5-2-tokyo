#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "Pillow>=10"]
# ///
"""Choose a repeatable visual audit sample across wards, real heights and photo coverage."""

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    heights = parser.add_mutually_exclusive_group()
    heights.add_argument(
        "--stage", type=Path, help="Directory of Blender ENU triangles."
    )
    heights.add_argument(
        "--heights", type=Path, help="Precomputed world vertex Z ranges by building ID."
    )
    parser.add_argument(
        "--before",
        required=True,
        type=Path,
        help="Baseline turntable report directory.",
    )
    parser.add_argument(
        "--views", required=True, type=Path, help="Turntable directory to inspect."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--seed", default="tokyo-paint-v2-audit", help="Stable hash namespace."
    )
    parser.add_argument(
        "--exclude-ids", type=Path, help="IDs used by earlier audits, one per line."
    )
    parser.add_argument(
        "--photo-only",
        action="store_true",
        help="Only sample buildings with source imagery.",
    )
    parser.add_argument(
        "--max-height", type=float, help="Exclusive upper bound on real vertex height."
    )
    parser.add_argument(
        "--per-photo-band",
        type=int,
        default=8,
        help="Number per ward and height band with source imagery.",
    )
    parser.add_argument(
        "--ward",
        action="append",
        choices=["13101", "13102", "13103"],
        help="Restrict the sample to a completed ward during an incremental export.",
    )
    parser.add_argument(
        "--sample",
        type=Path,
        help="Reuse an earlier sample CSV when checking revisions.",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.sample:
        with args.sample.open() as stream:
            selected = list(csv.DictReader(stream))
    else:
        if not args.stage and not args.heights:
            parser.error("Choosing a new sample requires --stage or --heights.")
        measured = json.loads(args.heights.read_text()) if args.heights else {}
        excluded = (
            set(args.exclude_ids.read_text().splitlines())
            if args.exclude_ids
            else set()
        )
        with (args.before / "candidates.csv").open() as stream:
            history = {r["id"]: r for r in csv.DictReader(stream)}
        pools = defaultdict(list)
        with (ROOT / "review/building-textures.csv").open() as stream:
            for row in csv.DictReader(stream):
                ident = row["building_id"]
                if ident in excluded or args.photo_only and row["coverage"] == "none":
                    continue
                if args.ward and ident[:5] not in args.ward:
                    continue
                if args.heights:
                    height = measured[ident]
                else:
                    with np.load(args.stage / row["tile"] / (ident + ".npz")) as data:
                        height = np.ptp(data["positions"][..., 2])
                if args.max_height is not None and height >= args.max_height:
                    continue
                tier = "<15" if height < 15 else "15–60" if height < 60 else "60+"
                ward = ident[:5]
                key = hashlib.sha256((args.seed + "/" + ident).encode()).hexdigest()
                entry = {
                    "id": ident,
                    "tile": row["tile"],
                    "height_m": f"{height:.2f}",
                    "ward": ward,
                    "band_m": tier,
                    "source_coverage": row["coverage"],
                    "prior_flag": int(bool(history[ident]["reasons"])),
                }
                pools[ward, tier, row["coverage"]].append((key, entry))
        selected = []
        source_groups = defaultdict(int)
        for ward, tier, coverage in pools:
            if coverage != "none":
                source_groups[ward, tier] += 1
        for group in sorted(pools):
            ordered = [entry for _, entry in sorted(pools[group])]
            clean = [entry for entry in ordered if not entry["prior_flag"]]
            # Reserve one previously unflagged building wherever the stratum contains one.
            unique = {e["id"]: e for e in ((clean[:1] if clean else []) + ordered)}
            ward, tier, coverage = group
            limit = (
                4
                if coverage == "none"
                else args.per_photo_band // source_groups[ward, tier]
            )
            selected.extend(list(unique.values())[:limit])
    with (args.output / "sample.csv").open("w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=selected[0], lineterminator="\n")
        writer.writeheader()
        writer.writerows(selected)
    for start in range(0, len(selected), 8):
        entries = selected[start : start + 8]
        canvas = Image.new("RGB", (4 * 252, 2 * 276), "#1f292e")
        draw = ImageDraw.Draw(canvas)
        for j, entry in enumerate(entries):
            image = Image.open(
                args.views / "buildings" / entry["tile"] / (entry["id"] + ".png")
            ).convert("RGB")
            image.thumbnail((244, 244))
            x = j % 4 * 252
            y = j // 4 * 276
            canvas.paste(image, (x + 4, y + 28))
            draw.text(
                (x + 4, y + 2), f"{entry['id']}  {entry['height_m']}m", fill="white"
            )
            draw.text(
                (x + 4, y + 14),
                f"{entry['source_coverage']}  prior flag={entry['prior_flag']}",
                fill="#ccd9df",
            )
        canvas.save(args.output / f"sample-{start // 8:02}.jpg", quality=94)
    print(f"{len(selected)} buildings sampled")


if __name__ == "__main__":
    main()
