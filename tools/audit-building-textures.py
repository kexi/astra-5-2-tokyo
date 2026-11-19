# /// script
# requires-python = ">=3.11"
# dependencies = ["DracoPy==2.2.0", "numpy==2.4.4"]
# ///
"""Audit building LOD and base-colour coverage in the distributed Tokyo Station GLBs."""

import argparse
import csv
import gzip
import hashlib
import io
import json
import struct
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import urlopen

import DracoPy
import numpy as np

ASSETS = Path(__file__).resolve().parents[1] / "assets"
AUDIT_CSV = ASSETS.parent / "review/building-textures.csv"
CACHE = Path(tempfile.gettempdir()) / "tokyo-flight-data/http"
SCALARS = {
    "BYTE": "i1",
    "UNSIGNED_BYTE": "u1",
    "SHORT": "<i2",
    "UNSIGNED_SHORT": "<u2",
    "INT": "<i4",
    "UNSIGNED_INT": "<u4",
}
GLTF_SCALARS = {
    5120: "i1",
    5121: "u1",
    5122: "<i2",
    5123: "<u2",
    5125: "<u4",
    5126: "<f4",
}


def read_source_tile(url, cache, fetch, include_geometry):
    source = cache / hashlib.sha256(url.encode()).hexdigest()
    if not source.exists():
        if not fetch:
            raise FileNotFoundError(
                f"Missing source tile. Use --fetch-missing to cache {url}"
            )
        cache.mkdir(parents=True, exist_ok=True)
        with urlopen(url, timeout=60) as response:
            content = response.read()
        if not content.startswith(b"b3dm"):
            raise ValueError(f"Expected b3dm from {url}")
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as tmp:
            tmp.write(content)
        Path(tmp.name).replace(source)
    with source.open("rb") as stream:
        magic, version, _, ft, fb, bt, bb = struct.unpack("<4s6I", stream.read(28))
        if (magic, version) != (b"b3dm", 1):
            raise ValueError(f"Unsupported b3dm header in {url}")
        count = json.loads(stream.read(ft))["BATCH_LENGTH"]
        stream.seek(fb, 1)
        table = json.loads(stream.read(bt))
        binary = stream.read(bb)
        geometry = memoryview(stream.read()) if include_geometry else None
    if len(table["gml_id"]) != count:
        raise ValueError(f"BATCH_LENGTH differs from the gml_id array in {url}")
    lod = table["_lod"]
    if isinstance(lod, dict):
        if lod["type"] != "SCALAR":
            raise ValueError(f"Unexpected _lod in {url}")
        table["_lod"] = np.frombuffer(
            binary,
            SCALARS[lod["componentType"]],
            len(table["gml_id"]),
            lod["byteOffset"],
        ).tolist()
    return table, geometry


def view(gltf, binary, index):
    item = gltf["bufferViews"][index]
    start = item.get("byteOffset", 0)
    return binary[start : start + item["byteLength"]]


def read_scalar(gltf, binary, index):
    accessor = gltf["accessors"][index]
    if accessor["type"] != "SCALAR" or "sparse" in accessor:
        raise ValueError(
            "Expected a dense scalar accessor for a face index or batch ID"
        )
    vi = accessor["bufferView"]
    dtype = np.dtype(GLTF_SCALARS[accessor["componentType"]])
    return np.ndarray(
        accessor["count"],
        dtype,
        view(gltf, binary, vi),
        accessor.get("byteOffset", 0),
        (gltf["bufferViews"][vi].get("byteStride", dtype.itemsize),),
    )


def triangle_features(gltf, binary, primitive):
    if primitive.get("mode", 4) != 4:
        raise ValueError("Expected triangle geometry")
    draco = primitive.get("extensions", {}).get("KHR_draco_mesh_compression")
    if draco:
        mesh = DracoPy.decode(bytes(view(gltf, binary, draco["bufferView"])))
        batches = mesh.get_attribute_by_unique_id(draco["attributes"]["_BATCHID"])[
            "data"
        ].ravel()
        indices = mesh.faces
    else:
        batches = read_scalar(gltf, binary, primitive["attributes"]["_BATCHID"])
        indices = (
            read_scalar(gltf, binary, primitive["indices"])
            if "indices" in primitive
            else np.arange(len(batches))
        )
    ids = batches[indices.reshape(-1, 3)]
    if not np.all(ids == ids[:, :1]):
        raise ValueError("A triangle has vertices from different buildings")
    feature_ids = ids[:, 0].astype(np.int64)
    if not np.all(ids[:, 0] == feature_ids):
        raise ValueError("Non-integer building ID")
    return feature_ids


def has_image(gltf, binary, primitive):
    if "material" not in primitive:
        return False
    pbr = gltf["materials"][primitive["material"]].get("pbrMetallicRoughness", {})
    ref = pbr.get("baseColorTexture")
    if ref is None:
        return False
    texture = gltf["textures"][ref["index"]]
    image = texture.get("extensions", {}).get("EXT_texture_webp", texture)
    payload = gltf["images"][image["source"]]
    uv = (
        ref.get("extensions", {})
        .get("KHR_texture_transform", {})
        .get("texCoord", ref.get("texCoord", 0))
    )
    uv_key = f"TEXCOORD_{uv}"
    accessor = gltf["accessors"][primitive["attributes"][uv_key]]
    draco = primitive.get("extensions", {}).get("KHR_draco_mesh_compression", {})
    has_uv = uv_key in draco.get("attributes", {}) or "bufferView" in accessor
    if not has_uv or "bufferView" not in payload:
        raise ValueError("Base-colour texture missing its UV set or embedded image")
    if not len(view(gltf, binary, payload["bufferView"])):
        raise ValueError("Empty base-colour image")
    return True


def face_counts(tile_id, packed, count):
    magic, version, length, js_length, _ = struct.unpack_from("<4s4I", packed)
    if (magic, version, length) != (b"glTF", 2, len(packed)):
        raise ValueError(f"Invalid GLB for {tile_id}")
    gltf = json.loads(bytes(packed[20 : 20 + js_length]))
    binary = packed[28 + js_length :]
    triangles = np.zeros((2, count), dtype=np.int64)
    for mesh in gltf["meshes"]:
        for primitive in mesh["primitives"]:
            ids = triangle_features(gltf, binary, primitive)
            if np.any((ids < 0) | (ids >= count)):
                raise ValueError(f"Invalid batch ID in {tile_id}")
            with_image = has_image(gltf, binary, primitive)
            triangles[int(with_image)] += np.bincount(ids, minlength=count)
    return triangles


def inspect_tile(tile, packed, cache, fetch, compare_source=False):
    table, source_glb = read_source_tile(tile["source"], cache, fetch, compare_source)
    count = len(table["gml_id"])
    lod_types = table.get("uro:lodType", [None] * count)
    triangles = face_counts(tile["id"], packed, count)
    if source_glb is not None:
        source_counts = face_counts(tile["id"], source_glb, count)
        if not np.array_equal(triangles, source_counts):
            raise ValueError(
                f"Source and distributed face counts differ by batch in {tile['id']}"
            )
    for i, gml_id in enumerate(table["gml_id"]):
        without_image, image = map(int, triangles[:, i])
        if not (without_image or image):
            raise ValueError(f"No faces for {gml_id} in {tile['id']}")
        yield (
            i,
            {
                "gml_id": gml_id,
                "building_id": table["uro:BuildingIDAttribute_uro:buildingID"][i],
                "name": table["gml:name"][i],
                "tile": tile["id"],
                "meshcode": table["meshcode"][i],
                "lod": table["_lod"][i],
                "lod_type": lod_types[i],
                "image_triangles": image,
                "other_triangles": without_image,
                "coverage": "all"
                if not without_image
                else "mixed"
                if image
                else "none",
            },
        )


def write_batch_index(path, reference, records, batches):
    source = reference.read_bytes()
    rows = csv.DictReader(io.StringIO(source.decode("utf-8-sig"), newline=""))
    positions = {}
    for position, row in enumerate(rows):
        gml_id = row["gml_id"]
        if gml_id in positions or gml_id not in records:
            raise ValueError(f"Duplicate or unknown CSV gml_id: {gml_id}")
        expected = {
            field: "" if value is None else str(value)
            for field, value in records[gml_id].items()
        }
        if row != expected:
            raise ValueError(
                f"CSV disagrees with source attributes or GLB faces for {gml_id}"
            )
        positions[gml_id] = position
    if len(positions) != len(records):
        raise ValueError(f"CSV is missing {len(records) - len(positions)} buildings")
    building_ids = {row["building_id"] for row in records.values()}
    if not all(building_ids) or len(building_ids) != len(records):
        raise ValueError(
            "Official building IDs do not uniquely identify the GML features"
        )
    # Batch numbers index the official b3dm table, while CSV rows may be in any order.
    tiles = {
        tile: [positions[gml_id] for gml_id in ids] for tile, ids in batches.items()
    }
    index = {
        "version": 1,
        "csvSha256": hashlib.sha256(source).hexdigest(),
        "tiles": tiles,
    }
    with (
        tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp,
        gzip.GzipFile(filename="", fileobj=tmp, mode="wb", mtime=0) as stream,
    ):
        stream.write(json.dumps(index, separators=(",", ":")).encode())
    staged = Path(tmp.name)
    staged.chmod(0o644)
    staged.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache", type=Path, default=CACHE, help="prepare-data.py HTTP cache"
    )
    parser.add_argument(
        "--fetch-missing",
        action="store_true",
        help="download any missing official b3dm tiles",
    )
    parser.add_argument(
        "--building", help="show an exact gml:id, buildingID or building name"
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--csv", type=Path, help="write one row per building ID")
    output.add_argument(
        "--batch-index",
        type=Path,
        metavar="FILE.json.gz",
        help="write official batch-index to zero-based CSV data row mappings after a full audit",
    )
    parser.add_argument(
        "--reference-csv",
        type=Path,
        default=AUDIT_CSV,
        help="CSV to verify and index (default: review/building-textures.csv)",
    )
    args = parser.parse_args()
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    tiles = [tile for tile in manifest["tiles"] if tile["kind"] == "building"]
    records = {}
    batches = {}
    for idx, archive in enumerate(manifest["archives"]):
        group = [tile for tile in tiles if tile["archive"] == idx]
        if not group:
            continue
        packed = memoryview(gzip.decompress((ASSETS / archive).read_bytes()))
        for tile in group:
            if tile["id"] in batches:
                raise ValueError(f"Repeated tile ID: {tile['id']}")
            ids = []
            batches[tile["id"]] = ids
            start = tile["offset"]
            for batch, row in inspect_tile(
                tile,
                packed[start : start + tile["length"]],
                args.cache,
                args.fetch_missing,
                compare_source=args.batch_index is not None,
            ):
                key = row["gml_id"]
                if key in records:
                    raise ValueError(f"Repeated building identity: {key}")
                if not key or batch != len(ids):
                    raise ValueError(
                        f"Unexpected b3dm feature table entry in {tile['id']}"
                    )
                ids.append(key)
                records[key] = row
    if len(batches) != len(tiles):
        raise ValueError("Not all building tiles refer to a manifest archive")
    if args.batch_index:
        write_batch_index(args.batch_index, args.reference_csv, records, batches)
    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(
                stream, next(iter(records.values())).keys(), lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(records.values())
    if args.building:
        matches = [
            r
            for r in records.values()
            if args.building in (r["gml_id"], r["building_id"], r["name"])
        ]
        if not matches:
            parser.exit(1, f"No building named {args.building}\n")
        result = matches
    else:
        groups = defaultdict(Counter)
        for row in records.values():
            labels = ("all", f"LOD{row['lod']}", f"uro:{row['lod_type'] or 'unset'}")
            for label in labels:
                counts = groups[label]
                counts["buildings"] += 1
                counts[row["coverage"]] += 1
                counts["image_triangles"] += row["image_triangles"]
                counts["other_triangles"] += row["other_triangles"]
        result = {"tiles": len(tiles), "by_group": groups}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
