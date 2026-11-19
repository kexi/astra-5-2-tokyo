#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "Pillow>=10"]
# ///
"""Check every published triangle, corner UV, normal and map against its Blender export."""

import argparse
import csv
import gzip
import io
import json
import runpy
import struct
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
flight_preview = runpy.run_path(str(ROOT / "tools/prepare-building-web.py"))[
    "flight_preview"
]


def segment(folder, files, address):
    pack, offset, size = address
    with (folder / files[pack]).open("rb") as stream:
        stream.seek(offset)
        data = stream.read(size)
    if len(data) != size:
        raise ValueError(f"Truncated segment: {address}")
    return data


def unpack(data):
    magic, n, n_indices, index_size = struct.unpack_from("<4s3I", data)
    if magic != b"BCT2" or index_size not in (2, 4):
        raise ValueError("Unexpected geometry header")
    offset = 16

    def take(dtype, count, stride=1):
        nonlocal offset
        block = np.frombuffer(data, dtype=dtype, count=count * stride, offset=offset)
        offset += (block.nbytes + 3) // 4 * 4
        return block.reshape(count, stride) if stride > 1 else block

    arrays = (
        take("<f4", n, 3),
        take("<i2", n, 3),
        take("<u2", n, 2),
        take("<u2", n, 2),
        take("u1", n),
        take(f"<u{index_size}", n_indices),
    )
    if offset != len(data):
        raise ValueError("Unexpected geometry length")
    return arrays


def overview_rectangle(uv, shared_uv, atlas_size):
    """Recover each building's actual texture address from its published corner attributes."""
    src = uv.astype(np.float64) / 65535
    dest = shared_uv.astype(np.float64) / 65535 * atlas_size
    if np.min(np.ptp(src, axis=0)) < 0.09:
        return None
    centered = src - src.mean(axis=0)
    scale = np.rint(
        (centered * (dest - dest.mean(axis=0))).sum(axis=0)
        / (centered * centered).sum(axis=0)
    ).astype(int)
    origin = np.rint((dest - src * scale).mean(axis=0)).astype(int)
    residual = np.max(np.abs(dest - origin - src * scale), axis=0)
    if (
        np.any(scale < 1)
        or np.any(origin < 0)
        or np.any(origin + scale > atlas_size)
        or np.any(residual > np.asarray(atlas_size) / 65535 + 0.001)
    ):
        raise ValueError(
            "Published aerial UVs do not share one upright texture rectangle"
        )
    x, y = origin
    w, h = scale
    return (x, atlas_size[1] - y - h, x + w, atlas_size[1] - y)


def audit(job):
    stage, folder, catalog, tile, source_ids, uncertain = job
    xyz, norm, uv, shared_uv, kinds, indices = unpack(
        gzip.decompress(segment(folder, catalog["overview"], tile["geometry"]))
    )
    atlas = Image.open(io.BytesIO(segment(folder, catalog["overview"], tile["atlas"])))
    atlas.load()
    if np.max(atlas.size) > 4096 or not np.any(shared_uv):
        raise ValueError(f"Invalid overview texture {tile['id']}")
    prev_v = prev_i = 0
    max_mae = (0.0, "")
    results = Counter()
    focus = catalog.get("focus") or {"files": [], "buildings": {}}
    focus_limits = {
        ident: 2048 if rank < 10 else 1536 if rank < 32 else 1024
        for rank, ident in enumerate(focus["buildings"])
    }
    for ident, _name, end_v, end_i, image, counts, ortho, prior, *flags in tile[
        "buildings"
    ]:
        regions = uncertain.get(ident)
        if flags != ([regions] if regions is not None else []):
            raise ValueError(
                f"Published review state differs from Blender registry for {ident}"
            )
        if regions is not None:
            results.update(buildingsWithUncertainPaint=1, unresolvedRegions=regions)
        original = source_ids[ident]
        if original["tile"] != tile["id"]:
            raise ValueError(f"Incorrect tile for {ident}")
        expected = int(original["image_triangles"]) + int(original["other_triangles"])
        selection = indices[prev_i:end_i]
        if (
            selection.min() < prev_v
            or selection.max() >= end_v
            or len(selection) != expected * 3
        ):
            raise ValueError(f"Incorrect building range for {ident}")
        rect = overview_rectangle(uv[selection], shared_uv[selection], atlas.size)
        path = stage / tile["id"] / ident
        with np.load(path.with_suffix(".npz")) as data:
            pos = data["positions"].reshape(-1, 3)
            normals = data["normals"].reshape(-1, 3)
            source_uv = data["uv"].reshape(-1, 2)
            kind = data["kind"]
            before = int(np.count_nonzero((kind == 0) & data["priorRepair"]))
            topo = int(np.count_nonzero(data["ortho"]))
            if not (
                np.array_equal(xyz[selection, 0], pos[:, 0])
                and np.array_equal(xyz[selection, 1], pos[:, 2])
                and np.array_equal(xyz[selection, 2], -pos[:, 1])
            ):
                raise ValueError(f"Blender world position mismatch for {ident}")
            restored = norm[selection].astype("f4") / 32767
            restored = restored[:, [0, 2, 1]]
            restored[:, 1] *= -1
            if (
                np.max(np.abs(restored - normals)) > 1.65e-5
                or np.max(np.abs(uv[selection].astype("f4") / 65535 - source_uv))
                > 8.2e-6
            ):
                raise ValueError(f"Blender UV/normal mismatch for {ident}")
            if (
                not np.array_equal(kinds[selection], kind.repeat(3))
                or np.bincount(kind, minlength=5).tolist() != counts
                or (topo, before) != (ortho, prior)
            ):
                raise ValueError(f"Blender face finish mismatch for {ident}")
        with (
            Image.open(path.with_suffix(".png")) as original,
            Image.open(io.BytesIO(segment(folder, catalog["photos"], image))) as mapped,
        ):
            if original.size != mapped.size:
                raise ValueError(f"Published map resolution decreased for {ident}")
            # A JPEG-like WebP encoder can change colors slightly at the same UV.
            a = np.asarray(original.convert("RGB"), dtype=np.int16)
            b = np.asarray(mapped.convert("RGB"), dtype=np.int16)
            error = float(np.mean(np.abs(a - b)))
            if error > 15:
                raise ValueError(
                    f"Published image differs from dedicated map for {ident}: mean absolute error {error}"
                )
            if rect is not None:
                crop = atlas.crop(rect).convert("RGB")
                reference = flight_preview(
                    original.convert("RGB"), source_uv, max(crop.size)
                )
                if crop.size != reference.size:
                    raise ValueError(f"Aerial texture aspect changed for {ident}")
                difference = np.asarray(crop, dtype=np.int16) - np.asarray(
                    reference, dtype=np.int16
                )
                if np.abs(difference).mean() > 15:
                    raise ValueError(f"Aerial UVs address the wrong paint for {ident}")
                results.update(verifiedAerialPaints=1)
            if ident in focus_limits:
                address = focus["buildings"][ident]
                with Image.open(
                    io.BytesIO(segment(folder, focus["files"], address))
                ) as focused:
                    reference = flight_preview(
                        original.convert("RGB"), source_uv, focus_limits[ident]
                    )
                    if focused.size != reference.size:
                        raise ValueError(
                            f"Incorrect flight texture resolution for {ident}"
                        )
                    difference = np.asarray(
                        focused.convert("RGB"), dtype=np.int16
                    ) - np.asarray(reference, dtype=np.int16)
                    if np.abs(difference).mean() > 15:
                        raise ValueError(
                            f"Incorrect flight texture contents for {ident}"
                        )
                results.update(verifiedFocusPaints=1)
            max_mae = max(max_mae, (error, ident))
        results.update(
            buildings=1, triangles=expected, imagePixels=a.shape[0] * a.shape[1]
        )
        prev_v, prev_i = end_v, end_i
    if prev_v != len(xyz) or prev_i != len(indices):
        raise ValueError(f"Orphaned geometry in {tile['id']}")
    return results, max_mae


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--assets", type=Path, default=ROOT / "assets/buildings")
    parser.add_argument(
        "--tiles", nargs="+", help="Restrict an audit to specified tiles."
    )
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument(
        "--screening",
        type=Path,
        default=ROOT / "tools/blender_texture_screening.json.gz",
    )
    args = parser.parse_args()
    with (ROOT / "review/building-textures.csv").open() as stream:
        ids = {r["building_id"]: r for r in csv.DictReader(stream)}
    catalog = json.loads(
        gzip.decompress((args.assets / "catalog.json.gz").read_bytes())
    )
    all_ids = [r[0] for t in catalog["tiles"] for r in t["buildings"]]
    if len(all_ids) != len(set(all_ids)) or catalog["count"] != len(all_ids):
        raise ValueError("Invalid or duplicated catalog entries")
    focus = catalog.get("focus")
    if focus and (
        len(focus["buildings"]) > 72 or not set(focus["buildings"]).issubset(all_ids)
    ):
        raise ValueError(
            "Flight textures reference unknown buildings or exceed the draw budget"
        )
    filter_tiles = set(args.tiles or [])
    tiles = [t for t in catalog["tiles"] if not filter_tiles or t["id"] in filter_tiles]
    checked_ids = {r[0] for t in tiles for r in t["buildings"]}
    expected = {
        id for id, r in ids.items() if not filter_tiles or r["tile"] in filter_tiles
    }
    if checked_ids != expected or (filter_tiles and len(tiles) != len(filter_tiles)):
        raise ValueError("PLATEAU inventory and published IDs differ")
    counts = Counter()
    max_mae = (0.0, "")
    uncertain = {}
    if args.screening.is_file():
        registry = json.loads(gzip.decompress(args.screening.read_bytes()))
        referenced = set(registry["uncertain"]) | set(registry.get("retained", {}))
        if registry["version"] != 1 or not referenced.issubset(ids):
            raise ValueError("Unknown wall review registry version or PLATEAU ID")
        uncertain = {
            ident: 0
            for ident, mark in registry.get("retained", {}).items()
            if mark == "uncertain"
        }
        uncertain.update(
            {
                ident: len(rects)
                for ident, rects in registry["uncertain"].items()
                if rects
            }
        )
    jobs = [(args.stage, args.assets, catalog, t, ids, uncertain) for t in tiles]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for count, metric in pool.map(audit, jobs):
            counts.update(count)
            max_mae = max(max_mae, metric)
    print(
        json.dumps(
            {**counts, "tiles": len(tiles), "worstMeanAbsoluteColorError": max_mae},
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
