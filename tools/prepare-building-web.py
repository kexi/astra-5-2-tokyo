#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "Pillow>=10"]
# ///
"""Pack Blender's ENU triangles and dedicated building maps for the public viewers."""

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import struct
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 8 * 1024**2
PADDING = 3
# Identity was verified by ray casting in review/building-textures.md.
STATION = {
    "13101-bldg-1663": "東京駅丸の内駅舎",
    "13101-bldg-3635": "東京駅八重洲口 グランルーフ",
}


def webp(image, quality):
    encoded = io.BytesIO()
    image.save(encoded, format="WEBP", quality=quality, method=3)
    return encoded.getvalue()


def pack_rectangles(images):
    area = sum((im.width + 2 * PADDING) * (im.height + 2 * PADDING) for im in images)
    width = 2 ** math.ceil(
        math.log2(
            max(max(im.width for im in images) + 2 * PADDING, math.sqrt(area * 1.16))
        )
    )
    positions = [None] * len(images)
    x = y = row_height = 0
    for idx in sorted(range(len(images)), key=lambda i: -images[i].height):
        im = images[idx]
        w, h = im.width + 2 * PADDING, im.height + 2 * PADDING
        if x + w > width:
            y += row_height
            x = row_height = 0
        positions[idx] = (x, y)
        x += w
        row_height = max(row_height, h)
    height = y + row_height
    atlas = Image.new("RGB", (width, height))
    for im, xy in zip(images, positions):
        padded = np.pad(
            np.asarray(im),
            ((PADDING, PADDING), (PADDING, PADDING), (0, 0)),
            mode="edge",
        )
        atlas.paste(Image.fromarray(padded), xy)
    return atlas, [(x + PADDING, y + PADDING) for x, y in positions]


def three_axes(values):
    result = values[..., [0, 2, 1]].copy()
    result[..., 2] *= -1
    return result


def flight_preview(image, uv, side):
    # Filtering sparse islands against black gaps darkens small buildings and distant roofs.
    pixels = np.asarray(image)
    alpha = Image.fromarray(np.any(pixels != 0, axis=2).astype(np.uint8) * 255)
    draw = ImageDraw.Draw(alpha)
    coords = (
        uv.reshape(-1, 3, 2) * (image.width, -image.height) + (0, image.height)
    ).astype(np.int32)
    for tri in coords:
        draw.polygon(tri.flatten().tolist(), fill=255)
    level = image.copy()
    level.putalpha(alpha)
    level.thumbnail((side, side), Image.Resampling.LANCZOS)
    pyramid = [level]
    while max(level.size) > 1:
        level = level.resize(
            (max(1, level.width // 2), max(1, level.height // 2)), Image.Resampling.BOX
        )
        pyramid.append(level)
    fallback = pyramid.pop().convert("RGB")
    for level in reversed(pyramid):
        fallback = fallback.resize(level.size, Image.Resampling.BILINEAR)
        fallback.paste(level, mask=level.getchannel("A"))
    return fallback


def build_tile(job):
    tile, rows, stage, work = job
    positions, normals, uvs, indices, previews, records = [], [], [], [], [], []
    kinds = []
    vertex_end = index_end = 0
    work.mkdir(parents=True, exist_ok=True)
    with (work / "photos").open("wb") as photos:
        for row in rows:
            ident = row["building_id"]
            path = stage / tile / ident
            with np.load(path.with_suffix(".npz")) as data:
                pos = three_axes(data["positions"]).astype("<f4").reshape(-1, 3)
                norm = three_axes(data["normals"]).reshape(-1, 3)
                uv = data["uv"].reshape(-1, 2)
                kind = data["kind"]
                mapped = np.bincount(kind, minlength=5)
                ortho = int(np.count_nonzero(data["ortho"]))
                prior = int(np.count_nonzero((kind == 0) & data["priorRepair"]))
            expected = int(row["image_triangles"]) + int(row["other_triangles"])
            if (
                len(pos) != expected * 3
                or not np.isfinite(pos).all()
                or not np.isfinite(norm).all()
                or not np.isfinite(uv).all()
            ):
                raise ValueError(
                    f"{ident}: invalid geometry or triangle count {len(pos) // 3}, expected {expected}"
                )
            if np.min(uv) < -0.00002 or np.max(uv) > 1.00002:
                raise ValueError(f"{ident}: UV exceeds dedicated map")
            vertices = np.empty(
                len(pos),
                dtype=[
                    ("p", "<f4", 3),
                    ("n", "<i2", 3),
                    ("u", "<u2", 2),
                    ("kind", "u1"),
                ],
            )
            vertices["p"] = pos
            vertices["n"] = np.rint(np.clip(norm, -1, 1) * 32767)
            vertices["u"] = np.rint(np.clip(uv, 0, 1) * 65535)
            vertices["kind"] = kind.repeat(3)
            unique, inverse = np.unique(vertices, return_inverse=True)
            positions.append(unique["p"])
            normals.append(unique["n"])
            uvs.append(unique["u"])
            kinds.append(unique["kind"])
            indices.append(inverse.ravel() + vertex_end)
            vertex_end += len(unique)
            index_end += len(pos)
            with Image.open(path.with_suffix(".png")) as source:
                image = source.convert("RGB")
            data = webp(image, 87)
            address = [photos.tell(), len(data)]
            photos.write(data)
            centre = (pos.max(axis=0) + pos.min(axis=0)) * 0.5
            near = math.hypot(centre[0], centre[2]) < 1000
            side = (
                1536
                if ident in STATION
                else 768
                if row["landmark"] or row["name"] == "東京駅"
                else round(
                    np.clip(np.ptp(pos, axis=0).max() * (1.5 if near else 0.7), 32, 256)
                )
            )
            previews.append(flight_preview(image, uv, side))
            records.append(
                [
                    ident,
                    row["name"],
                    vertex_end,
                    index_end,
                    address,
                    mapped.tolist(),
                    ortho,
                    prior,
                ]
            )
    atlas, xy = pack_rectangles(previews)
    if max(atlas.size) > 4096:
        raise ValueError(f"{tile}: atlas exceeds WebGL budget: {atlas.size}")
    atlas_uvs = []
    for uv, image, (x, y) in zip(uvs, previews, xy):
        normalized = uv.astype("float32") / 65535
        scaled = normalized * image.size + (x, atlas.height - y - image.height)
        atlas_uvs.append(np.rint(scaled / atlas.size * 65535).astype("<u2"))
    width = 2 if vertex_end <= 65536 else 4
    parts = [
        np.vstack(positions),
        np.vstack(normals),
        np.vstack(uvs),
        np.vstack(atlas_uvs),
        np.concatenate(kinds),
        np.concatenate(indices).astype(f"<u{width}"),
    ]
    with (
        (work / "geometry").open("wb") as raw,
        gzip.GzipFile(
            filename="", fileobj=raw, mode="wb", compresslevel=6, mtime=0
        ) as geometry,
    ):
        geometry.write(struct.pack("<4s3I", b"BCT2", vertex_end, index_end, width))
        for array in parts:
            block = array.tobytes()
            geometry.write(block)
            geometry.write(bytes((-len(block)) % 4))
    (work / "atlas").write_bytes(webp(atlas, 87))
    return tile, records, work


def cached_tile(job):
    tile, rows, stage, work = job
    stamp = hashlib.sha256(Path(__file__).read_bytes().split(b"def cached_tile(")[0])
    stamp.update(str(stage.resolve()).encode())
    for row in rows:
        stamp.update(json.dumps(row, sort_keys=True).encode())
        for ext in (".png", ".npz"):
            stat = (stage / tile / (row["building_id"] + ext)).stat()
            stamp.update(struct.pack("<QQ", stat.st_size, stat.st_mtime_ns))
    key = stamp.hexdigest()
    meta = work / "tile.json"
    if meta.is_file():
        cached = json.loads(meta.read_text())
        if cached["key"] == key and all(
            (work / f).is_file() for f in ("geometry", "atlas", "photos")
        ):
            return tile, cached["records"], work
    _, records, _ = build_tile(job)
    meta.write_text(json.dumps({"key": key, "records": records}, separators=(",", ":")))
    return tile, records, work


class Packs:
    def __init__(self, output, prefix):
        self.output = output
        self.prefix = prefix
        self.files = []
        self.handle = None

    def append(self, blob):
        if self.handle is None or self.handle.tell() + len(blob) > LIMIT:
            self.close()
            name = f"{self.prefix}-{len(self.files):03}.bin"
            self.files.append(name)
            self.handle = (self.output / name).open("wb")
            self.digest = hashlib.sha256()
        address = [len(self.files) - 1, self.handle.tell(), len(blob)]
        self.handle.write(blob)
        self.digest.update(blob)
        return address

    def close(self):
        if self.handle:
            self.handle.close()
            original = self.files[-1]
            name = (
                original.removesuffix(".bin")
                + "-"
                + self.digest.hexdigest()[:12]
                + ".bin"
            )
            (self.output / original).rename(self.output / name)
            self.files[-1] = name
            self.handle = None


def roads(output):
    manifest = json.loads((ROOT / "assets/manifest.json").read_text())
    offsets = {}
    files = []
    for i, name in enumerate(manifest["archives"]):
        tiles = [
            t
            for t in manifest["tiles"]
            if t["kind"] != "building" and t["archive"] == i
        ]
        if not tiles:
            continue
        source = gzip.decompress((ROOT / "assets" / name).read_bytes())
        chunk = bytearray()
        for t in tiles:
            offsets[t["id"]] = [len(files), len(chunk), t["length"]]
            chunk.extend(source[t["offset"] : t["offset"] + t["length"]])
        file = f"infrastructure-{i:02}.bin.gz"
        files.append(file)
        (output / file).write_bytes(gzip.compress(chunk, compresslevel=6, mtime=0))
    return {"files": files, "tiles": offsets}


def flight_details(output, catalog, stage):
    import subprocess

    probe = output / "catalog.json.gz"
    probe.write_bytes(gzip.compress(json.dumps(catalog).encode(), mtime=0))
    chosen = output / "flight-details.json"
    subprocess.run(
        [
            "node",
            str(ROOT / "tools/measure-building-flight.mjs"),
            str(output),
            str(chosen),
        ],
        check=True,
    )
    selected = json.loads(chosen.read_text())
    paths = {
        row[0]: stage / tile["id"] / row[0]
        for tile in catalog["tiles"]
        for row in tile["buildings"]
    }
    maps = Packs(output, "focus")
    addresses = {}
    for rank, ident in enumerate(selected):
        path = paths[ident]
        side = 2048 if rank < 10 else 1536 if rank < 32 else 1024
        with (
            Image.open(path.with_suffix(".png")) as source,
            np.load(path.with_suffix(".npz")) as data,
        ):
            resampled = flight_preview(source.convert("RGB"), data["uv"], side)
            addresses[ident] = maps.append(webp(resampled, 90))
    maps.close()
    return {"files": maps.files, "buildings": addresses}


def main():
    import tempfile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "assets/buildings")
    parser.add_argument(
        "--tile", action="append", help="Test export to an alternate output directory."
    )
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument(
        "--screening",
        type=Path,
        default=ROOT / "tools/blender_texture_screening.json.gz",
        help="Blender wall review registry used to mark photographs with unresolved regions.",
    )
    parser.add_argument(
        "--cache", type=Path, help="Reuse encoded tiles whose inputs are unchanged."
    )
    parser.add_argument(
        "--prepare-available",
        action="store_true",
        help="Warm the cache while Blender is exporting.",
    )
    args = parser.parse_args()
    if args.tile and args.output.resolve() == (ROOT / "assets/buildings").resolve():
        parser.error("Partial exports require a separate --output.")
    names = {
        b["id"]: b["name"]
        for b in json.loads((ROOT / "tools/omy-buildings.json").read_text())
    }
    landmark_ids = set(names)
    names.update(STATION)
    groups = defaultdict(list)
    with (ROOT / "review/building-textures.csv").open() as source:
        for row in csv.DictReader(source):
            if args.tile and row["tile"] not in args.tile:
                continue
            row["landmark"] = row["building_id"] in names
            row["name"] = names.get(row["building_id"], row["name"])
            groups[row["tile"]].append(row)
    if not groups or (args.tile and set(args.tile) != set(groups)):
        parser.error("Unknown tile.")
    if args.prepare_available:
        if not args.cache:
            parser.error("--prepare-available requires --cache.")
        jobs = [
            (tile, rows, args.stage, args.cache / tile)
            for tile, rows in groups.items()
            if (args.stage / tile).is_dir()
            and all(
                (args.stage / tile / (row["building_id"] + ext)).is_file()
                for row in rows
                for ext in (".png", ".npz")
            )
        ]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            total = sum(len(records) for _, records, _ in pool.map(cached_tile, jobs))
        print(
            json.dumps({"cachedTiles": len(jobs), "cachedBuildings": total}), flush=True
        )
        return
    args.output.mkdir(parents=True, exist_ok=True)
    uncertain = {}
    if args.screening.is_file():
        screening = json.loads(gzip.decompress(args.screening.read_bytes()))
        if screening["version"] != 1:
            parser.error("Unknown wall review registry version.")
        uncertain = {
            ident: len(regions)
            for ident, regions in screening["uncertain"].items()
            if regions
        }
        for ident, verdict in screening.get("retained", {}).items():
            if verdict == "uncertain":
                uncertain.setdefault(ident, 0)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as scratch:
        temp = Path(scratch)
        overview, maps = Packs(temp, "overview"), Packs(temp, "images")
        tiles = []
        jobs = [
            (tile, rows, args.stage, (args.cache or temp) / tile)
            for tile, rows in groups.items()
        ]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for tile, records, work in pool.map(
                cached_tile if args.cache else build_tile, jobs
            ):
                with (work / "photos").open("rb") as photos:
                    for row in records:
                        offset, size = row[4]
                        photos.seek(offset)
                        row[4] = maps.append(photos.read(size))
                        if row[0] in uncertain:
                            row.append(uncertain[row[0]])
                tiles.append(
                    {
                        "id": tile,
                        "geometry": overview.append((work / "geometry").read_bytes()),
                        "atlas": overview.append((work / "atlas").read_bytes()),
                        "buildings": records,
                    }
                )
        overview.close()
        maps.close()
        infra = roads(temp) if not args.tile else None
        count = sum(len(t["buildings"]) for t in tiles)
        catalog = {
            "version": 1,
            "count": count,
            "landmarks": sum(
                record[0] in landmark_ids
                for tile in tiles
                for record in tile["buildings"]
            ),
            "overview": overview.files,
            "photos": maps.files,
            "tiles": tiles,
            "infrastructure": infra,
        }
        if not args.tile:
            catalog["focus"] = flight_details(temp, catalog, args.stage)
        focus_files = catalog.get("focus", {}).get("files", [])
        encoded = json.dumps(
            catalog, separators=(",", ":"), ensure_ascii=False
        ).encode()
        (temp / "catalog.json.gz").write_bytes(gzip.compress(encoded, mtime=0))
        previous = args.output / "catalog.json.gz"
        old_packs = set()
        if previous.is_file():
            old = json.loads(gzip.decompress(previous.read_bytes()))
            old_packs = set(
                old["overview"] + old["photos"] + old.get("focus", {}).get("files", [])
            )
        for name in (
            overview.files
            + maps.files
            + focus_files
            + (infra["files"] if infra else [])
            + ["catalog.json.gz"]
        ):
            os.replace(temp / name, args.output / name)
        for name in old_packs.difference(overview.files, maps.files, focus_files):
            if (
                Path(name).name == name
                and name.startswith(("images-", "overview-", "focus-"))
                and name.endswith(".bin")
            ):
                (args.output / name).unlink(missing_ok=True)
        print(
            json.dumps(
                {
                    "buildings": count,
                    "tiles": len(tiles),
                    "overviewPacks": len(overview.files),
                    "photoPacks": len(maps.files),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
