# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = []
#
# [tool.blender]
# version = "5.2"
# ///
"""Compare split Blender buildings to the previous scene and official PLATEAU tiles.

Run with Blender's Python after reopening the saved result:
  blender -b output/tokyo-station.blend --python-exit-code 1 \
    -P tools/audit-blender-buildings.py -- --baseline /tmp/tokyo-before-all-buildings.blend

The original b3dm files must be in the prepare-data.py HTTP cache. Use
--cache for another cache and --report for a machine-readable result.
"""

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import struct
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import cache
from itertools import pairwise, product
from pathlib import Path

import bpy
import numpy as np
from io_scene_gltf2.blender.imp.draco_compression_extension import decode_primitive
from io_scene_gltf2.io.imp.gltf2_io_binary import BinaryData
from io_scene_gltf2.io.imp.gltf2_io_gltf import glTFImporter

ROOT = Path(__file__).resolve().parents[1]
CITY = "01  PLATEAU・建築物"
LANDMARKS = "10  大丸有・建物別の外装写真"
WARDS = {"chiyoda": "千代田区", "chuo": "中央区", "minato": "港区"}
HTTP_CACHE = Path(tempfile.gettempdir()) / "tokyo-flight-data/http"
POSITION = 0.001  # metres, allowing for object-local float32 recentering in Blender
UV = 0.000002
NORMAL = 0.002  # distance between unit vectors, about 0.115 degrees
CORNERS = np.arange(3)
NEIGHBORS = tuple(product((-1, 0, 1), repeat=3))
SCALARS = {
    "BYTE": "i1",
    "UNSIGNED_BYTE": "u1",
    "SHORT": "<i2",
    "UNSIGNED_SHORT": "<u2",
    "INT": "<i4",
    "UNSIGNED_INT": "<u4",
}


def need(condition, message):
    if not condition:
        raise ValueError(message)


def as_array(items, prop, width=1, dtype=np.float32):
    values = np.empty((len(items), width), dtype=dtype)
    items.foreach_get(prop, values.ravel())
    return values if width != 1 else values[:, 0]


@cache
def image_signature(image):
    files = tuple(
        hashlib.sha256(entry.packed_file.data).hexdigest()
        for entry in image.packed_files
    )
    need(files, f"Unpacked material image: {image.name}")
    return tuple(image.size), image.colorspace_settings.name, image.alpha_mode, files


def socket_value(socket):
    value = getattr(socket, "default_value", None)
    if isinstance(value, (float, int, bool, str)) or value is None:
        return value
    return tuple(value)


@cache
def graph_signature(graph):
    properties = (
        "operation",
        "blend_type",
        "data_type",
        "interpolation",
        "interpolation_type",
        "projection",
        "projection_blend",
        "extension",
        "uv_map",
        "is_active_output",
        "clamp",
        "use_clamp",
        "mode",
    )
    nodes = []
    for node in sorted(graph.nodes, key=lambda item: item.name):
        image = getattr(node, "image", None)
        subgroup = getattr(node, "node_tree", None)
        nodes.append(
            (
                node.name,
                node.bl_idname,
                node.mute,
                tuple(getattr(node, prop, None) for prop in properties),
                tuple(socket_value(socket) for socket in node.inputs),
                image_signature(image) if image else None,
                graph_signature(subgroup) if subgroup else None,
            )
        )
    links = sorted(
        (
            link.from_node.name,
            link.from_socket.identifier,
            link.to_node.name,
            link.to_socket.identifier,
        )
        for link in graph.links
    )
    return hashlib.sha256(repr((nodes, links)).encode()).hexdigest()


@cache
def material_signature(material):
    if material is None:
        return None, False
    graph = material.node_tree
    photo = bool(graph and any(n.type == "TEX_IMAGE" and n.image for n in graph.nodes))
    settings = (
        tuple(material.diffuse_color),
        material.roughness,
        material.metallic,
        material.surface_render_method,
        material.use_backface_culling,
        graph_signature(graph) if graph else None,
    )
    return hashlib.sha256(repr(settings).encode()).hexdigest(), photo


@dataclass
class Triangles:
    xyz: np.ndarray
    vertices: np.ndarray
    edges: np.ndarray
    uv: dict[str, np.ndarray]
    uv_settings: tuple
    normals: np.ndarray
    custom_normals: bool
    materials: np.ndarray
    photos: np.ndarray
    smooth: np.ndarray
    batches: np.ndarray | None

    def subset(self, indices):
        return Triangles(
            self.xyz[indices],
            self.vertices[indices],
            self.edges[indices],
            {name: values[indices] for name, values in self.uv.items()},
            self.uv_settings,
            self.normals[indices],
            self.custom_normals,
            self.materials[indices],
            self.photos[indices],
            self.smooth[indices],
            self.batches[indices] if self.batches is not None else None,
        )


def mesh_triangles(obj, matrix):
    mesh = obj.data
    mesh.calc_loop_triangles()
    loops = as_array(mesh.loop_triangles, "loops", 3, np.int32)
    vertices = as_array(mesh.loops, "vertex_index", dtype=np.int32)[loops]
    edges = as_array(mesh.loops, "edge_index", dtype=np.int32)[loops]
    transform = np.asarray(matrix, dtype=np.float64)
    linear = transform[:3, :3]
    positions = as_array(mesh.vertices, "co", 3) @ linear.T + transform[:3, 3]
    normals = as_array(mesh.corner_normals, "vector", 3) @ np.linalg.inv(linear)
    lengths = np.linalg.norm(normals, axis=1)
    np.divide(normals, lengths[:, None], out=normals, where=lengths[:, None] > 0)
    layers = {
        layer.name: as_array(layer.uv, "vector", 2)[loops] for layer in mesh.uv_layers
    }
    uv_settings = (
        mesh.uv_layers.active.name if mesh.uv_layers.active else None,
        tuple((layer.name, layer.active_render) for layer in mesh.uv_layers),
    )
    signatures = [material_signature(slot.material) for slot in obj.material_slots]
    slots = as_array(mesh.loop_triangles, "material_index", dtype=np.int32)
    materials = np.array(
        [material[0] for material in signatures] or [None], dtype=object
    )
    photos = np.array([material[1] for material in signatures] or [False])
    polygons = as_array(mesh.loop_triangles, "polygon_index", dtype=np.int32)
    smooth = as_array(mesh.polygons, "use_smooth", dtype=bool)[polygons]
    batch_attribute = mesh.attributes.get("_BATCHID")
    batches = None
    if batch_attribute:
        need(batch_attribute.domain == "POINT", f"{obj.name}: non-point _BATCHID")
        corners = as_array(batch_attribute.data, "value")[vertices]
        batches = corners[:, 0].astype(np.int32)
        need(np.all(corners == batches[:, None]), f"{obj.name}: invalid _BATCHID")
    need(
        np.isfinite(positions).all()
        and np.isfinite(normals).all()
        and all(np.isfinite(layer).all() for layer in layers.values()),
        f"{obj.name}: non-finite geometry, UV or normal",
    )
    return Triangles(
        positions[vertices],
        vertices,
        edges,
        layers,
        uv_settings,
        normals[loops],
        mesh.has_custom_normals,
        materials[slots],
        photos[slots],
        smooth,
        batches,
    )


def match_triangles(reference, actual, label):
    """Match the same winding despite cyclic corner rotation or triangle reordering."""
    need(reference.shape == actual.shape, f"{label}: triangle count changed")
    if not len(reference):
        return np.empty(0, dtype=int), np.empty(0, dtype=int), 0.0
    errors = np.stack(
        [
            np.linalg.norm(reference - actual[:, (CORNERS + shift) % 3], axis=2).max(
                axis=1
            )
            for shift in CORNERS
        ]
    )
    rotations = errors.argmin(axis=0)
    direct = np.arange(len(reference))
    if errors.min(axis=0).max() <= POSITION:
        return direct, rotations, float(errors.min(axis=0).max())

    def cells(points):
        return np.floor(points.mean(axis=1) / POSITION).astype(np.int64)

    by_cell = defaultdict(list)
    for index, cell in enumerate(cells(actual)):
        by_cell[tuple(cell)].append(index)
    matched = np.empty(len(reference), dtype=np.int64)
    taken = set()
    maximum = 0.0
    for index, cell in enumerate(cells(reference)):
        candidates = [
            candidate
            for shift in NEIGHBORS
            for candidate in by_cell.get(tuple(cell + shift), ())
            if candidate not in taken
        ]
        need(
            candidates,
            f"{label}: missing triangle near {reference[index].mean(axis=0)}",
        )
        ordered = actual[
            np.array(candidates)[:, None, None], (CORNERS[:, None] + CORNERS) % 3
        ]
        distances = np.linalg.norm(ordered - reference[index], axis=-1).max(axis=-1)
        chosen, rotation = np.unravel_index(distances.argmin(), distances.shape)
        error = float(distances[chosen, rotation])
        need(
            error <= POSITION,
            f"{label}: triangle moved or winding reversed ({error:.6g} m)",
        )
        match = candidates[chosen]
        taken.add(match)
        matched[index], rotations[index] = match, rotation
        maximum = max(maximum, error)
    return matched, rotations, maximum


def compare_attributes(reference, actual, label, summary):
    matched, rotation, error = match_triangles(reference.xyz, actual.xyz, label)
    corners = (CORNERS + rotation[:, None]) % 3
    matches = matched[:, None], corners

    def incidence(indices):
        _, first, inverse = np.unique(
            indices.ravel(), return_index=True, return_inverse=True
        )
        return first.argsort().argsort()[inverse]

    need(
        np.array_equal(
            incidence(reference.vertices), incidence(actual.vertices[matches])
        ),
        f"{label}: mesh vertex connections changed",
    )
    need(
        np.array_equal(incidence(reference.edges), incidence(actual.edges[matches])),
        f"{label}: mesh edge connections changed",
    )
    need(reference.uv_settings == actual.uv_settings, f"{label}: UV layers changed")
    need(
        reference.custom_normals == actual.custom_normals,
        f"{label}: custom normals changed",
    )
    uv_error = max(
        (
            float(np.abs(values - actual.uv[name][matches]).max())
            for name, values in reference.uv.items()
        ),
        default=0.0,
    )
    normal_error = float(
        np.linalg.norm(reference.normals - actual.normals[matches], axis=2).max()
    )
    need(uv_error <= UV, f"{label}: UV coordinates changed by {uv_error:.6g}")
    need(
        normal_error <= NORMAL, f"{label}: corner normals changed by {normal_error:.6g}"
    )
    need(
        np.array_equal(reference.smooth, actual.smooth[matched]),
        f"{label}: smoothing changed",
    )
    need(
        np.array_equal(reference.materials, actual.materials[matched]),
        f"{label}: face materials or their packed images changed",
    )
    summary["max_baseline_position_error_m"] = max(
        summary["max_baseline_position_error_m"], error
    )
    summary["max_uv_error"] = max(summary["max_uv_error"], uv_error)
    summary["max_normal_error"] = max(summary["max_normal_error"], normal_error)
    return matched


def earth_frame(origin):
    lon, lat = map(math.radians, origin[:2])
    height = origin[2]
    sl, cl, sp, cp = math.sin(lon), math.cos(lon), math.sin(lat), math.cos(lat)
    eccentricity = 0.00669437999014
    radius = 6378137 / math.sqrt(1 - eccentricity * sp**2)
    ecef = np.array(
        [
            (radius + height) * cp * cl,
            (radius + height) * cp * sl,
            (radius * (1 - eccentricity) + height) * sp,
        ]
    )
    enu = np.array([[-sl, cl, 0], [-sp * cl, -sp * sl, cp], [cp * cl, cp * sl, sp]])
    return ecef, enu


def read_source(tile, cached, temp, frame):
    source = tile["source"]
    path = cached / hashlib.sha256(source.encode()).hexdigest()
    need(path.is_file(), f"Missing b3dm for {tile['id']}: {path}")
    data = path.read_bytes()
    magic, version, length, ft, fb, bt, bb = struct.unpack_from("<4s6I", data)
    need(
        (magic, version, length) == (b"b3dm", 1, len(data)),
        f"{tile['id']}: invalid b3dm",
    )
    table_start = 28 + ft + fb
    table_binary = table_start + bt
    metadata = json.loads(data[table_start:table_binary])
    lod = metadata["_lod"]
    if isinstance(lod, dict):
        need(lod["type"] == "SCALAR", f"{tile['id']}: non-scalar _lod")
        metadata["_lod"] = np.frombuffer(
            data,
            SCALARS[lod["componentType"]],
            len(metadata["gml_id"]),
            table_binary + lod["byteOffset"],
        ).tolist()

    glb_start = table_binary + bb
    glb_length = struct.unpack_from("<I", data, glb_start + 12)[0]
    raw_glb = json.loads(data[glb_start + 20 : glb_start + 20 + glb_length])
    feature = json.loads(data[28 : 28 + ft]) if ft else {}
    rtc = np.asarray(feature.get("RTC_CENTER", (0, 0, 0)), dtype=np.float64)
    rtc += raw_glb.get("extensions", {}).get("CESIUM_RTC", {}).get("center", (0, 0, 0))
    need(
        np.allclose(rtc, tile["center"], atol=1e-6, rtol=0),
        f"{tile['id']}: RTC changed",
    )
    need(
        all(set(node) == {"mesh"} for node in raw_glb["nodes"]),
        f"{tile['id']}: unexpected node transform in original tile",
    )

    # Decode without creating Blender meshes. The geometry importer's custom-attribute
    # filter can drop unsigned batch IDs, but the GLB accessor still holds them.
    temp.write_bytes(data[glb_start:])
    importer = glTFImporter(str(temp), {"import_user_extensions": []})
    importer.read()
    ecef, enu = frame
    faces, batches, photos = [], [], []
    for node in raw_glb["nodes"]:
        for primitive in importer.data.meshes[node["mesh"]].primitives:
            need(primitive.mode in (None, 4), f"{tile['id']}: non-triangle primitive")
            if (
                primitive.extensions
                and "KHR_draco_mesh_compression" in primitive.extensions
            ):
                decode_primitive(importer, primitive)
            attr = primitive.attributes
            positions = BinaryData.decode_accessor(importer, attr["POSITION"]).astype(
                np.float64
            )
            ids = BinaryData.decode_accessor(importer, attr["_BATCHID"]).ravel()
            indices = (
                BinaryData.decode_accessor(importer, primitive.indices).reshape(-1, 3)
                if primitive.indices is not None
                else np.arange(len(ids)).reshape(-1, 3)
            )
            corners = ids[indices]
            batch = corners[:, 0].astype(np.int32)
            need(np.all(corners == batch[:, None]), f"{tile['id']}: invalid face batch")
            world = (positions[:, [0, 2, 1]] * [1, -1, 1] + (rtc - ecef)) @ enu.T
            faces.append(world[indices])
            batches.append(batch)
            photo = primitive.material is not None and bool(
                raw_glb["materials"][primitive.material]
                .get("pbrMetallicRoughness", {})
                .get("baseColorTexture")
            )
            photos.append(np.full(len(batch), photo, dtype=bool))
    return (
        np.concatenate(faces),
        np.concatenate(batches),
        np.concatenate(photos),
        metadata,
    )


def split_rows(batches):
    if not len(batches):
        return {}
    order = np.argsort(batches, kind="stable")
    sorted_ids = batches[order]
    starts = np.flatnonzero(np.r_[True, sorted_ids[1:] != sorted_ids[:-1], True])
    return {
        int(sorted_ids[start]): order[start:stop] for start, stop in pairwise(starts)
    }


def group_meshes(collection):
    return [obj for obj in collection.all_objects if obj.type == "MESH"]


def read_baseline(path):
    with bpy.data.libraries.load(str(path)) as (source, loaded):
        need(
            {CITY, LANDMARKS} <= set(source.collections),
            "Baseline is missing building collections",
        )
        loaded.collections = [CITY, LANDMARKS]
    city, landmarks = loaded.collections
    tiles = {obj.name.removeprefix("PLATEAU・"): obj for obj in group_meshes(city)}
    faces = {obj["gml"]: obj for obj in group_meshes(landmarks)}
    need(len(tiles) == len(group_meshes(city)), "Duplicate baseline building tile")
    need(len(faces) == len(group_meshes(landmarks)), "Duplicate baseline landmark")
    need(
        all(obj.parent is None for obj in [*tiles.values(), *faces.values()]),
        "Expected unparented objects in the saved pre-split scene",
    )
    return tiles, faces


def load_records():
    data = (ROOT / "review/building-textures.csv").read_bytes()
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    manifest = json.loads((ROOT / "assets/manifest.json").read_text())
    index = json.loads(
        gzip.decompress((ROOT / "tools/building-batches.json.gz").read_bytes())
    )
    need(index["version"] == 1, "Unsupported building batch index version")
    need(
        hashlib.sha256(data).hexdigest() == index["csvSha256"],
        "Building CSV differs from the official batch index",
    )
    tiles = {
        tile["id"]: tile for tile in manifest["tiles"] if tile["kind"] == "building"
    }
    need(
        tiles.keys() == index["tiles"].keys(),
        "Manifest building tiles differ from the batch index",
    )
    need(
        sorted(row for tile in index["tiles"].values() for row in tile)
        == list(range(len(rows))),
        "The batch index duplicates or omits a CSV entry",
    )
    return rows, tiles, index["tiles"], earth_frame(manifest["origin"])


def candidates(scene, rows, tiles):
    subdivision = bpy.data.collections[CITY]
    objects = set(group_meshes(subdivision))
    landmarks = set(group_meshes(bpy.data.collections[LANDMARKS]))
    need(landmarks <= objects, "Landmarks are not linked in the city hierarchy")
    groups = {}
    for ward, name in WARDS.items():
        group = subdivision.children[name]
        for tile in group.children:
            need(tile.name.startswith(f"{ward}-"), f"{tile.name}: wrong ward")
            groups[tile.name] = tile
    need(groups.keys() == tiles.keys(), "Missing or extra tile collections")
    need(
        sum(len(group.objects) for group in groups.values()) == len(objects),
        "An object appears in multiple tile collections or outside its tile",
    )
    expected = {row["gml_id"] for row in rows}
    database = {obj.get("gml"): obj for obj in objects}
    need(len(expected) == len(rows), "Duplicate official GML ID")
    need(
        len(database) == len(objects) and database.keys() == expected,
        f"Expected one object per GML ID: missing={len(expected - database.keys())}, "
        f"unexpected={len(database.keys() - expected)}, duplicate={len(objects) - len(database)}",
    )
    need(
        len({obj.data for obj in objects}) == len(objects),
        "Multiple buildings share a mesh datablock",
    )
    need(
        all(
            not obj.hide_render and obj.visible_get() and not obj.modifiers
            for obj in objects
        ),
        "Found a hidden building or unapplied geometry modifier",
    )
    tile_sources = {tile["source"] for tile in tiles.values()}
    unexpected = [
        obj.name
        for obj in scene.objects
        if obj.type == "MESH"
        and obj.get("gml") not in expected
        and (
            obj.get("source") in tile_sources
            or obj.name.removeprefix("PLATEAU・").partition(".")[0] in tiles
        )
    ]
    need(
        not unexpected,
        f"Original building tiles still appear in the scene: {unexpected[:8]}",
    )
    need(
        sum(obj.get("gml") in expected for obj in scene.objects) == len(objects),
        "An additional scene object duplicates a GML ID",
    )
    return database


def verify_metadata(obj, row, tile, batch, metadata):
    expected = {
        "gml": row["gml_id"],
        "id": row["building_id"],
        "tile": tile["id"],
        "batch": batch,
        "lod": int(row["lod"]),
        "lod_type": row["lod_type"],
        "meshcode": row["meshcode"],
        "coverage": row["coverage"],
        "imageFaces": int(row["image_triangles"]),
        "faces": int(row["image_triangles"]) + int(row["other_triangles"]),
        "source": tile["source"],
    }
    for name, value in expected.items():
        need(
            obj.get(name) == value,
            f"{obj.name}: {name} {obj.get(name)!r}, expected {value!r}",
        )
    official = {
        "gml_id": metadata["gml_id"][batch],
        "building_id": metadata["uro:BuildingIDAttribute_uro:buildingID"][batch],
        "lod": str(metadata["_lod"][batch]),
        "lod_type": str(
            metadata.get("uro:lodType", [None] * len(metadata["gml_id"]))[batch] or ""
        ),
        "meshcode": str(metadata["meshcode"][batch]),
        "name": str(metadata["gml:name"][batch] or ""),
    }
    for name, value in official.items():
        need(
            row[name] == value,
            f"{obj.name}: official {name} differs from the audit CSV",
        )


def audit_tile(
    tile,
    batch_rows,
    records,
    database,
    baseline,
    landmarks,
    cached,
    temp,
    frame,
    summary,
):
    tile_id = tile["id"]
    source_xyz, source_batches, source_photos, metadata = read_source(
        tile, cached, temp, frame
    )
    source_groups = split_rows(source_batches)
    need(
        len(metadata["gml_id"]) == len(batch_rows)
        and source_groups.keys() == set(range(len(batch_rows))),
        f"{tile_id}: missing official triangle batch",
    )
    previous = mesh_triangles(baseline, baseline.matrix_basis)
    if previous.batches is None:
        # Independently decode the official face ownership for tiles whose original
        # Blender import dropped their unsigned batch attribute.
        old_rows, _, _ = match_triangles(
            source_xyz, previous.xyz, f"{tile_id} baseline"
        )
        previous_groups = {
            batch: old_rows[indices] for batch, indices in source_groups.items()
        }
        summary["recovered_baseline_tiles"].append(tile_id)
    else:
        previous_groups = split_rows(previous.batches)
    for batch, row_index in enumerate(batch_rows):
        record = records[row_index]
        key = record["gml_id"]
        obj = database[key]
        need(
            obj.name in bpy.data.collections[tile_id].objects,
            f"{obj.name}: outside its source tile's collection",
        )
        verify_metadata(obj, record, tile, batch, metadata)
        actual = mesh_triangles(obj, obj.matrix_world)
        need(
            len(np.unique(actual.vertices)) == len(obj.data.vertices)
            and len(np.unique(actual.edges)) == len(obj.data.edges),
            f"{obj.name}: unexpected loose vertices or edges",
        )
        official_indices = source_groups[batch]
        official_photo_count = int(source_photos[official_indices].sum())
        need(
            len(actual.xyz) == len(official_indices) == obj["faces"]
            and int(actual.photos.sum()) == official_photo_count == obj["imageFaces"],
            f"{obj.name}: photo/plain face counts differ from the source",
        )
        matched, _, error = match_triangles(
            source_xyz[official_indices], actual.xyz, f"{obj.name} official"
        )
        need(
            np.array_equal(source_photos[official_indices], actual.photos[matched]),
            f"{obj.name}: coverage differs on individual triangles",
        )
        summary["max_official_position_error_m"] = max(
            summary["max_official_position_error_m"], error
        )
        need(
            actual.batches is not None and np.all(actual.batches == batch),
            f"{obj.name}: retained mesh batch attribute disagrees",
        )
        if key in landmarks:
            old = landmarks[key]
            compare_attributes(
                mesh_triangles(old, old.matrix_basis), actual, obj.name, summary
            )
        else:
            compare_attributes(
                previous.subset(previous_groups.pop(batch)), actual, obj.name, summary
            )
        summary["buildings"] += 1
        summary["image_triangles"] += official_photo_count
        summary["other_triangles"] += len(official_indices) - official_photo_count
        summary["by_lod"][record["lod"]] += 1
    need(not previous_groups, f"{tile_id}: extra meshes in the baseline tile indices")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--cache", type=Path, default=HTTP_CACHE)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    scene = bpy.context.scene
    need(
        Path(bpy.data.filepath).resolve() != args.baseline.resolve(),
        "Open the new model, not the baseline",
    )
    bpy.context.view_layer.update()
    records, tiles, batches, frame = load_records()
    database = candidates(scene, records, tiles)
    before, landmarks = read_baseline(args.baseline)
    need(before.keys() == tiles.keys(), "The baseline lacks selected PLATEAU tiles")
    summary = {
        "candidate": bpy.data.filepath,
        "baseline": str(args.baseline.resolve()),
        "tiles": len(tiles),
        "buildings": 0,
        "image_triangles": 0,
        "other_triangles": 0,
        "by_lod": Counter(),
        "recovered_baseline_tiles": [],
        "max_official_position_error_m": 0.0,
        "max_baseline_position_error_m": 0.0,
        "max_uv_error": 0.0,
        "max_normal_error": 0.0,
    }
    with tempfile.TemporaryDirectory(prefix="audit-blender-buildings-") as directory:
        temp = Path(directory) / "original.glb"
        for count, (tile_id, tile) in enumerate(tiles.items(), 1):
            audit_tile(
                tile,
                batches[tile_id],
                records,
                database,
                before[tile_id],
                landmarks,
                args.cache,
                temp,
                frame,
                summary,
            )
            if count % 40 == 0:
                print(f"Audited {count}/{len(tiles)} origin tiles", flush=True)
    report = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(report + "\n", encoding="utf-8")
    print(report, flush=True)


if __name__ == "__main__":
    main()
