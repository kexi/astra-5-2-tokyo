# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = ["numpy==2.3.4", "OpenImageIO==3.1.13.1"]
# ///
"""Prepare reviewed painting operations on an exported first draft using Blender's Python."""

import argparse
import gzip
import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from functools import cache
from pathlib import Path

import numpy as np
import OpenImageIO as oiio
from blender_texture_reviews import PLANS, recipe_hash, repaint


@cache
def implementation():
    digest = hashlib.sha256()
    for name in (
        "blender_texture_reviews.py",
        "blender_texture_charts.py",
        "omy_facades.py",
    ):
        source = Path(__file__).with_name(name).read_bytes()
        if name == "blender_texture_reviews.py":
            # A new hand-reviewed building does not change another ID's recipe.
            # Its own plans and manual/automatic method are hashed by recipe_hash.
            source = source[source.index(b"def covered_patch(") :]
        digest.update(source)
    return digest.digest()


def source_stamp(identifier, atlas, uv, positions, kind):
    stamp = hashlib.sha256(implementation())
    stamp.update(recipe_hash(identifier).encode())
    for data, dtype in ((atlas, "u1"), (uv, "<f4"), (positions, "<f4"), (kind, "u1")):
        stamp.update(np.asarray(data.shape, dtype="<i8").tobytes())
        stamp.update(np.ascontiguousarray(data, dtype=dtype))
    return stamp.hexdigest()


def prepare(job):
    source, output = job
    identifier = source.stem
    reader = oiio.ImageInput.open(str(source.with_suffix(".png")))
    if reader is None:
        raise ValueError(f"Unreadable input photograph for {identifier}")
    try:
        photo = np.ascontiguousarray(reader.read_image(oiio.UINT8)[::-1, :, :3])
    finally:
        reader.close()
    with np.load(source, allow_pickle=False) as mesh:
        uv, positions, kind = mesh["uv"], mesh["positions"], mesh["kind"]
    stamp = source_stamp(identifier, photo, uv, positions, kind)
    dest = output / source.parent.name / source.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        with np.load(dest, allow_pickle=False) as old:
            if "quality" in old and old["stamp"].item() == stamp:
                return
    quality = {}
    atlas, mapped, reasons = repaint(
        identifier, photo, uv, positions, kind, diagnostics=quality
    )
    # PNG and the stage use the opposite row convention to Blender's pixel buffer.
    # A lossless compressed in-memory array preserves even uncovered atlas texels.
    temp = dest.with_suffix(f".{os.getpid()}.partial")
    with temp.open("wb") as file:
        np.savez(
            file,
            stamp=stamp,
            quality=json.dumps(quality, allow_nan=False),
            shape=atlas.shape,
            rgb=np.frombuffer(
                gzip.compress(atlas.tobytes(), compresslevel=1, mtime=0), dtype="u1"
            ),
            uv=mapped,
            reasons=reasons,
        )
    temp.replace(dest)


def load_result(path, identifier, photo, uv, positions, kind, *, diagnostics):
    if path.is_file():
        with np.load(path, allow_pickle=False) as ready:
            if "quality" in ready and ready["stamp"].item() == source_stamp(
                identifier, photo, uv, positions, kind
            ):
                atlas = np.frombuffer(
                    gzip.decompress(ready["rgb"].tobytes()), dtype="u1"
                ).reshape(ready["shape"])
                diagnostics.update(json.loads(ready["quality"].item()))
                return atlas, ready["uv"], ready["reasons"]
    return repaint(identifier, photo, uv, positions, kind, diagnostics=diagnostics)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--ids", nargs="+", help="Limit a test to these reviewed IDs")
    parser.add_argument(
        "--available",
        action="store_true",
        help="Warm already finished tiles of a running first draft",
    )
    args = parser.parse_args()
    identifiers = set(args.ids or PLANS)
    paths = sorted(
        path for path in args.stage.glob("*/*.npz") if path.stem in identifiers
    )
    if args.available:
        import time

        cutoff = time.time() - 30
        paths = [p for p in paths if p.stat().st_mtime < cutoff]
    missing = identifiers - {path.stem for path in paths}
    if missing and not args.available:
        raise SystemExit(f"Incomplete first draft: {len(missing)} buildings missing")
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for done, _ in enumerate(
            pool.map(prepare, ((p, args.output) for p in paths), chunksize=4), 1
        ):
            if done % 500 == 0 or done == len(paths):
                print(
                    json.dumps({"prepared": done, "buildings": len(paths)}), flush=True
                )
