# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = []
#
# [tool.blender]
# version = "5.2"
# ///
"""Compare the saved painted city with the previous meshes and the render inputs."""

import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import bpy
import numpy as np
import OpenImageIO as oiio

ROOT = Path(__file__).resolve().parents[1]
CITY = "01  PLATEAU・建築物"
UV = "BuildingPaint"
QUALITY_COUNTS = (
    "evaluatedPixels",
    "retainedPixels",
    "appliedPixels",
    "rejectedDomains",
)


def check(value, message):
    if not value:
        raise ValueError(message)


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def values(items, prop, width=1, dtype=np.float32):
    dest = np.empty((len(items), width), dtype=dtype)
    items.foreach_get(prop, dest.ravel())
    return dest.squeeze(axis=1) if width == 1 else dest


def same(left, right, label, tolerance=0):
    equal = left.shape == right.shape and np.allclose(
        left, right, rtol=0, atol=tolerance
    )
    if not equal:
        if left.shape != right.shape:
            raise ValueError(f"{label}: {left.shape} != {right.shape}")
        difference = np.abs(left.astype(np.float64) - right.astype(np.float64))
        raise ValueError(f"{label}: max difference {np.max(difference):.9g}")


def compare_mesh(obj, old):
    mesh, previous = obj.data, old.data
    for key in ("id", "gml", "tile", "batch", "lod", "meshcode", "source"):
        check(obj[key] == old[key], key)
    same(np.asarray(obj.matrix_basis), np.asarray(old.matrix_basis), "object transform")
    for collection, attribute, width, dtype in (
        ("vertices", "co", 3, np.float32),
        ("edges", "vertices", 2, np.int32),
        ("loops", "vertex_index", 1, np.int32),
        ("polygons", "loop_start", 1, np.int32),
        ("polygons", "loop_total", 1, np.int32),
        ("corner_normals", "vector", 3, np.float32),
    ):
        left = values(getattr(mesh, collection), attribute, width, dtype)
        right = values(getattr(previous, collection), attribute, width, dtype)
        same(left, right, collection, 2e-6 if collection == "corner_normals" else 0)
    for layer in previous.uv_layers:
        same(
            values(mesh.uv_layers[layer.name].uv, "vector", 2),
            values(layer.uv, "vector", 2),
            layer.name,
        )
    return mesh


def image_on_faces(obj):
    mesh = obj.data
    check(mesh.uv_layers[UV].active_render, "render UV layer")
    assigned = set()
    slots = values(mesh.polygons, "material_index", dtype=np.int32)
    kinds = values(mesh.attributes["TextureKind"].data, "value", dtype=np.int32)
    for slot in np.unique(slots):
        nodes = mesh.materials[int(slot)].node_tree.nodes
        bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
        tex = bsdf.inputs["Base Color"].links[0].from_node
        check(tex.type == "TEX_IMAGE" and tex.label == obj["id"], "base color texture")
        coord = tex.inputs["Vector"].links[0].from_node
        check(coord.type == "UVMAP" and coord.uv_map == UV, "material UV route")
        if np.any(kinds[slots == slot] > 0):
            relief = bsdf.inputs["Normal"].links[0].from_node
            check(
                relief.type == "GROUP"
                and relief.node_tree.name == "PLATEAU · Building paint relief"
                and bsdf.inputs["Roughness"].links[0].from_node == relief
                and relief.inputs["Paint"].links[0].from_node == tex,
                "painted surface relief, roughness and atlas route",
            )
        assigned.add(tex.image)
    check(len(assigned) == 1, "multiple images in base color")
    image = assigned.pop()
    check(
        image.packed_file and Path(image.filepath).stem == obj["id"], "packed image ID"
    )
    return image


def compare_image(image, path):
    reader = oiio.ImageInput.open(str(path))
    if reader is None:
        raise ValueError(f"Cannot open {path.name}")
    try:
        staged = reader.read_image(oiio.UINT8)[:, :, :3]
    finally:
        reader.close()
    width, height = image.size
    check(staged.shape == (height, width, 3), "image dimensions")
    rgba = np.empty((height, width, 4), dtype=np.float32)
    image.pixels.foreach_get(rgba.ravel())
    actual = np.rint(np.clip(rgba[::-1, :, :3], 0, 1) * 255).astype(np.uint8)
    check(np.array_equal(actual, staged), "packed and staged RGB pixels differ")
    image.buffers_free()
    return width, height, hashlib.sha256(actual.tobytes()).hexdigest()


def area_vectors(coords):
    points = coords.reshape(-1, 3, 3).astype(np.float64)
    return np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])


def compare_export(obj, path):
    mesh = obj.data
    world = np.asarray(obj.matrix_world, dtype=np.float64)
    vertices = values(mesh.vertices, "co", 3)
    positions = vertices @ world[:3, :3].T + world[:3, 3]
    loops = values(mesh.loops, "vertex_index", dtype=np.int32)
    normals = values(mesh.corner_normals, "vector", 3) @ np.linalg.inv(world[:3, :3])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-20)
    uv = values(mesh.uv_layers[UV].uv, "vector", 2)
    check(np.all((uv >= 0) & (uv <= 1)), "non-finite or out-of-atlas UV")
    with np.load(path, allow_pickle=False) as data:
        nearest = positions[loops].astype("f4")
        exported = data["positions"].reshape(-1, 3)
        check(exported.dtype == np.dtype("float32"), "stage GPU position type")
        check(exported.shape == nearest.shape, "stage geometry shape")
        if not np.array_equal(nearest, exported):
            # The adjacent GPU float can preserve winding on submillimeter roof strips.
            reference = area_vectors(positions[loops])
            initial = area_vectors(nearest)
            original_winding = np.sum(reference * initial, axis=1) > 0
            fixed = area_vectors(exported)
            check(
                np.all(np.sum(reference * fixed, axis=1)[original_winding] > 0),
                "stage winding on neighboring faces",
            )
            failed_roofs = data["ortho"] & (
                initial[:, 2] <= 0.45 * np.linalg.norm(initial, axis=1)
            )
            candidates = positions[loops.reshape(-1, 3)[failed_roofs]].reshape(-1, 3)
            corners = positions[loops]
            changed = np.unique(corners[np.any(nearest != exported, axis=1)], axis=0)
            for point in changed:
                check(
                    np.any(np.all(candidates == point, axis=1)),
                    "stage adjustment outside a thin roof",
                )
                shared_points = exported[np.all(corners == point, axis=1)]
                check(
                    np.all(shared_points == shared_points[0]),
                    "stage crack at a shared position",
                )
            low = np.nextafter(nearest, np.float32(-np.inf))
            high = np.nextafter(nearest, np.float32(np.inf))
            check(
                np.all((exported >= low) & (exported <= high)), "stage vertex precision"
            )
            order = np.argsort(loops)
            shared = np.diff(loops[order]) == 0
            check(
                np.all(np.diff(exported[order], axis=0)[shared] == 0),
                "stage shared vertex",
            )
        same(normals, data["normals"].reshape(-1, 3), "stage corner normals", 2e-6)
        same(uv, data["uv"].reshape(-1, 2), "stage corner UV")
        for key, attribute in (
            ("kind", "TextureKind"),
            ("repairReason", "TextureRepairReason"),
            ("ortho", "TextureOrtho"),
            ("priorRepair", "TexturePriorRepair"),
        ):
            attr = mesh.attributes[attribute]
            check(attr.domain == "FACE", attribute)
            same(values(attr.data, "value", dtype=np.int32), data[key], key)
        kind, ortho = data["kind"], data["ortho"].astype(bool)
        check(np.all(kind <= 4), "unknown surface kind")
        check(np.all(kind[ortho] == 2), "orthophoto mapped outside a roof")
        for name, coords in (("Blender", positions[loops]), ("Web", exported)):
            cross = area_vectors(coords.reshape(-1, 3, 3)[ortho])
            check(
                np.all(cross[:, 2] > 0.45 * np.linalg.norm(cross, axis=1)),
                f"{name}: orthophoto on a wall, degenerate face or underside",
            )
        counts = np.bincount(kind, minlength=5)
        stats = {
            "photo": int(counts[0]),
            "wall": int(counts[1]),
            "roof": int(counts[2]),
            "soffit": int(counts[3]),
            "repair": int(counts[4]),
            "ortho": int(ortho.sum()),
            "priorRepair": int(data["priorRepair"].sum()),
            "repairFromPhoto": int(obj["textureRepairPhotoFaces"]),
        }
    for key, prop in (
        ("photo", "texturePhotoFaces"),
        ("ortho", "textureOrthoFaces"),
        ("priorRepair", "texturePriorRepairFaces"),
        ("repair", "textureRepairFaces"),
    ):
        check(stats[key] == obj[prop], key)
    check(sum(counts[1:4]) == obj["textureInferredFaces"], "infill face count")
    labels, amounts = np.unique(
        values(mesh.attributes["TextureRepairReason"].data, "value", dtype=np.int32),
        return_counts=True,
    )
    return stats, {int(k): int(v) for k, v in zip(labels, amounts, strict=True) if k}


def buildings(collection):
    objects = list(collection.all_objects)
    by_id = {o["id"]: o for o in objects}
    check(len(objects) == len(by_id), "duplicate PLATEAU building ID")
    return by_id


def main(args):
    scene = bpy.context.scene
    new = buildings(scene.collection.children[CITY])
    print(json.dumps({"event": "painted-inventory", "buildings": len(new)}), flush=True)
    # Appending would make 23,115 read-only reference meshes local one at a time.
    with bpy.data.libraries.load(str(args.baseline), link=True) as (src, dst):
        check(CITY in src.collections, "missing baseline city")
        dst.collections = [CITY]
    original = buildings(dst.collections[0])
    print(
        json.dumps({"event": "reference-inventory", "buildings": len(original)}),
        flush=True,
    )
    with (ROOT / "review/building-textures.csv").open() as stream:
        official = {row["building_id"] for row in csv.DictReader(stream)}
    check(new.keys() == original.keys() == official, "building inventory mismatch")
    rows, images, hashes = [], set(), defaultdict(list)
    staged_hash = hashlib.sha256()
    reasons = Counter()
    quality_totals = Counter()
    largest_contrast = (0.0, None)
    for identifier, obj in sorted(new.items()):
        if args.tiles and obj["tile"] not in args.tiles:
            continue
        try:
            compare_mesh(obj, original[identifier])
            check(obj["textureBuildingId"] == identifier, "textureBuildingId")
            image = image_on_faces(obj)
            check(image not in images, "image also assigned to another building")
            images.add(image)
            path = args.stage / obj["tile"] / identifier
            stats, building_reasons = compare_export(obj, path.with_suffix(".npz"))
            reasons.update(building_reasons)
            width, height, digest = compare_image(image, path.with_suffix(".png"))
            geometry_hash = file_hash(path.with_suffix(".npz"))
            image_hash = file_hash(path.with_suffix(".png"))
            staged_hash.update(
                f"{identifier}\t{geometry_hash}\t{image_hash}\n".encode()
            )
            hashes[width, height, digest].append(identifier)
            quality = json.loads(obj.get("textureScreenQuality", "{}"))
            if quality:
                quality_totals["recordedBuildings"] += 1
                for key in QUALITY_COUNTS:
                    check(isinstance(quality[key], int) and quality[key] >= 0, key)
                    quality_totals[key] += quality[key]
                check(
                    quality["evaluatedPixels"]
                    == quality["retainedPixels"] + quality["appliedPixels"],
                    "paint mask accounting",
                )
                quality_totals["assessedBuildings"] += int(
                    quality["evaluatedPixels"] > 0
                )
                quality_totals["changedBuildings"] += int(quality["appliedPixels"] > 0)
                contrast = quality["peakAddedBoundaryContrast"]
                check(
                    np.isfinite(contrast) and contrast >= 0, "paint boundary contrast"
                )
                if contrast > largest_contrast[0]:
                    largest_contrast = (contrast, identifier)
            rows.append(
                dict(
                    id=identifier,
                    tile=obj["tile"],
                    lod=obj["lod"],
                    width=width,
                    height=height,
                    rgb_sha256=digest,
                    screenState=obj.get("textureScreenState", "unclassified"),
                    uncertainRegions=len(
                        json.loads(obj.get("textureUncertainRegions", "[]"))
                    ),
                    **stats,
                    **{
                        key: quality.get(key, "")
                        for key in (*QUALITY_COUNTS, "peakAddedBoundaryContrast")
                    },
                )
            )
        except Exception as exc:
            raise ValueError(f"{identifier} ({obj['tile']}): {exc}") from exc
        if len(rows) % 2000 == 0:
            print(json.dumps({"verified": len(rows)}), flush=True)
    check(rows, "no buildings checked")
    keys = (
        "photo",
        "wall",
        "roof",
        "soffit",
        "repair",
        "ortho",
        "priorRepair",
        "repairFromPhoto",
    )
    report = {
        "model": args.model_label,
        "blender_sha256": file_hash(bpy.data.filepath),
        "stage_sha256": staged_hash.hexdigest(),
        "buildings": len(rows),
        "packedBuildingImages": len(images),
        "distinctDecodedImages": len(hashes),
        "identicalPixels": [ids for ids in hashes.values() if len(ids) > 1],
        "registeredSurfaceFaces": {k: sum(r[k] for r in rows) for k in keys},
        "buildingsBySurface": {k: sum(r[k] > 0 for r in rows) for k in keys},
        "screenStates": dict(Counter(r["screenState"] for r in rows)),
        "buildingsWithUncertainRegions": sum(r["uncertainRegions"] > 0 for r in rows),
        "repairFacesByReason": dict(sorted(reasons.items())),
        "localRepairQuality": {
            **quality_totals,
            "peakAddedBoundaryContrast": largest_contrast[0],
            "peakBuilding": largest_contrast[1],
        },
        "lod": dict(Counter(row["lod"] for row in rows)),
        "comparison": "All original mesh vertices, edges, corner indices and UVs, transforms and IDs. Original and exported corner normals within 2e-6. Exported positions, building UVs, face classifications and decoded RGB checked per building.",
    }
    if args.csv:
        with (
            args.csv.open("wb") as archive,
            gzip.GzipFile(
                fileobj=archive, filename="", mode="wb", mtime=0
            ) as compressed,
            io.TextIOWrapper(compressed, encoding="utf-8", newline="") as stream,
        ):
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps({k: v for k, v in report.items() if k != "identicalPixels"}),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument(
        "--tiles", nargs="+", help="Limit a development check to these tiles"
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--model-label", default="tokyo-station-textures-v2")
    main(parser.parse_args(sys.argv[sys.argv.index("--") + 1 :]))
