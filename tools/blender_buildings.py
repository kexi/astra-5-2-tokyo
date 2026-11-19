"""Separate the imported PLATEAU triangles by their official building feature."""

import csv
import gzip
import hashlib
import io
import json
import struct
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
WARDS = {"chiyoda": "千代田区", "chuo": "中央区", "minato": "港区"}


def importable_building_glb(glb):
    """Ask Blender's Draco reader to return uint16 feature IDs as exact floats."""
    end = 20 + struct.unpack_from("<I", glb, 12)[0]
    doc = json.loads(glb[20:end])
    changed = False
    for mesh in doc["meshes"]:
        for prim in mesh["primitives"]:
            attr = doc["accessors"][prim["attributes"]["_BATCHID"]]
            if attr["componentType"] != 5123:
                continue
            if "_BATCHID" not in prim.get("extensions", {}).get(
                "KHR_draco_mesh_compression", {}
            ).get("attributes", {}):
                raise ValueError("Expected a compressed uint16 batch attribute")
            # The Blender glTF importer silently drops unsigned-short custom
            # scalars. Its Draco reader can decode the same bytes to float32.
            attr["componentType"] = 5126
            changed = True
    if not changed:
        return glb
    header = json.dumps(doc, separators=(",", ":")).encode()
    header += b" " * (-len(header) % 4)
    size = 20 + len(header) + len(glb) - end
    return (
        struct.pack("<4sIIII", b"glTF", 2, size, len(header), 0x4E4F534A)
        + header
        + glb[end:]
    )


def building_rows():
    index = json.loads(
        gzip.decompress((HERE / "building-batches.json.gz").read_bytes())
    )
    raw_csv = (HERE.parent / "review/building-textures.csv").read_bytes()
    if (
        index["version"] != 1
        or hashlib.sha256(raw_csv).hexdigest() != index["csvSha256"]
    ):
        raise ValueError("Building index and texture audit must be generated together")
    records = list(csv.DictReader(io.StringIO(raw_csv.decode())))
    return {
        tile: [records[i] for i in indices] for tile, indices in index["tiles"].items()
    }


def identify(obj, record, batch, source):
    face_count = int(record["image_triangles"]) + int(record["other_triangles"])
    if len(obj.data.polygons) != face_count:
        raise ValueError(
            f"{record['gml_id']}: expected {face_count} faces, got {len(obj.data.polygons)}"
        )
    obj["id"] = record["building_id"]
    obj["gml"] = record["gml_id"]
    obj["tile"] = record["tile"]
    obj["batch"] = batch
    obj["lod"] = int(record["lod"])
    obj["lod_type"] = record["lod_type"]
    obj["meshcode"] = record["meshcode"]
    obj["coverage"] = record["coverage"]
    obj["imageFaces"] = int(record["image_triangles"])
    obj["faces"] = face_count
    obj["source"] = source
    if record["name"]:
        obj["sourceName"] = record["name"]


def read_values(data, field, dtype, width=1):
    values = np.empty((len(data), width), dtype=dtype)
    data.foreach_get(field, values.ravel())
    return values


def tile_arrays(mesh):
    # Preserve edge and corner order, including disconnected faces whose
    # positions coincide at a roof or facade seam.
    coords = read_values(mesh.vertices, "co", np.float32, 3)
    edge_vertices = read_values(mesh.edges, "vertices", np.int32, 2)
    corner_vertices = read_values(mesh.loops, "vertex_index", np.int32).reshape(-1, 3)
    corner_edges = read_values(mesh.loops, "edge_index", np.int32).reshape(-1, 3)
    if len(mesh.polygons) != len(corner_vertices):
        raise ValueError(f"Expected triangle mesh: {mesh.name}")
    settings = {
        "INT": ("value", np.int32, 1),
        "FLOAT": ("value", np.float32, 1),
        "BOOLEAN": ("value", bool, 1),
        "FLOAT2": ("vector", np.float32, 2),
        "BYTE_COLOR": ("color_srgb", np.float32, 4),
    }
    attrs = {}
    for a in mesh.attributes:
        if a.name.startswith(".") or a.name == "position":
            continue
        if a.name == "custom_normal":
            # Packed int16 normals are relative to the face. Moving the mesh
            # origin changes Blender's floating-point evaluation of nearly
            # collinear triangles. A native float corner normal keeps the
            # actual source direction on those faces as well.
            attrs[a.name] = (
                "FLOAT_VECTOR",
                "CORNER",
                "vector",
                read_values(mesh.corner_normals, "vector", np.float32, 3),
            )
            continue
        field, dtype, width = settings[a.data_type]
        attrs[a.name] = (
            a.data_type,
            a.domain,
            field,
            read_values(a.data, field, dtype, width),
        )
    return coords, edge_vertices, corner_vertices, corner_edges, attrs


def building_mesh(name, arrays, faces, materials):
    coords, edge_vertices, corner_vertices, corner_edges, attrs = arrays
    vertices, v_indices = np.unique(corner_vertices[faces], return_inverse=True)
    edges, e_indices = np.unique(corner_edges[faces], return_inverse=True)
    vc = coords[vertices]
    # An integral translation near the bounds keeps the origin useful when
    # rotating a building and avoids introducing submillimetre rounding noise.
    origin = np.rint((vc.min(axis=0) + vc.max(axis=0)) * 0.5)
    corner_ids = (faces[:, None] * 3 + np.arange(3)).ravel()
    indices = {"POINT": vertices, "CORNER": corner_ids, "EDGE": edges, "FACE": faces}

    result = bpy.data.meshes.new(name)
    result.vertices.add(len(vertices))
    result.edges.add(len(edges))
    result.loops.add(len(corner_ids))
    result.polygons.add(len(faces))
    result.vertices.foreach_set("co", (vc - origin).ravel())
    result.edges.foreach_set(
        "vertices",
        np.searchsorted(vertices, edge_vertices[edges]).astype(np.int32).ravel(),
    )
    result.loops.foreach_set("vertex_index", v_indices.astype(np.int32).ravel())
    result.loops.foreach_set("edge_index", e_indices.astype(np.int32).ravel())
    result.polygons.foreach_set(
        "loop_start", np.arange(0, len(corner_ids), 3, dtype=np.int32)
    )
    for material in materials:
        result.materials.append(material)
    for attr_name, (kind, domain, field, values) in attrs.items():
        target = result.attributes.get(attr_name)
        if target is None:
            target = result.attributes.new(attr_name, kind, domain)
        target.data.foreach_set(field, values[indices[domain]].ravel())
    result.update()
    return result, origin


def separate_tile(original, group, records, landmarks):
    tile_id = records[0]["tile"]
    mesh = original.data
    expected = set(range(len(records))) - landmarks.keys()
    if not expected:
        if len(mesh.polygons):
            raise ValueError(f"Unexpected residual mesh: {tile_id}")
        return
    arrays = tile_arrays(mesh)
    _, _, corners, _, attributes = arrays
    batch = attributes["_BATCHID"][3].ravel()
    corner_batch = batch[corners]
    ids = corner_batch[:, 0].astype(np.int32)
    if not np.all(corner_batch == ids[:, None]) or set(np.unique(ids)) != expected:
        raise ValueError(f"Building feature mismatch: {tile_id}")
    order = np.argsort(ids, kind="stable")
    boundaries = np.flatnonzero(np.diff(ids[order])) + 1
    transform = original.matrix_world.copy()
    for faces in np.split(order, boundaries):
        key = int(ids[faces[0]])
        row = records[key]
        name = row["building_id"] + ("  " + row["name"] if row["name"] else "")
        piece, origin = building_mesh(name, arrays, faces, mesh.materials)
        obj = bpy.data.objects.new(name, piece)
        obj.matrix_world = transform @ Matrix.Translation(Vector(origin))
        identify(obj, row, key, original["source"])
        group.objects.link(obj)


def separate_buildings(root, landmarks):
    catalog = building_rows()
    featured = {}
    for obj in landmarks.objects:
        tile, batch = obj["tile"], obj["batch"]
        row = catalog[tile][batch]
        if obj["id"] != row["building_id"] or obj["gml"] != row["gml_id"]:
            raise ValueError(f"Unexpected identity for {obj.name}")
        identify(obj, row, batch, obj["source"])
        # Keep the landmark's familiar object name. Its mesh datablock can be
        # found by the official ID in the Outliner as well.
        obj.data.name = row["building_id"] + " " + obj.name
        featured.setdefault(tile, {})[batch] = obj

    wards = {}
    obsolete = []
    started = time.monotonic()
    tiles = list(root.objects)
    for original in tiles:
        tile_id = original.name.removeprefix("PLATEAU・")
        ward = tile_id.split("-")[0]
        if ward not in wards:
            wards[ward] = bpy.data.collections.new(WARDS[ward])
        group = bpy.data.collections.new(tile_id)
        local_landmarks = featured.get(tile_id, {})
        separate_tile(original, group, catalog[tile_id], local_landmarks)
        for obj in local_landmarks.values():
            group.objects.link(obj)
        wards[ward].children.link(group)
        obsolete.extend((original, original.data))
    # Link finished collections once, after populating their objects. This
    # avoids rebuilding a 23,000-object view layer after every building.
    bpy.data.batch_remove(ids=obsolete)
    for ward in wards.values():
        root.children.link(ward)
    print(
        f"Separated {len(root.all_objects)} buildings from {len(tiles)} tiles in {time.monotonic() - started:.1f} s",
        flush=True,
    )
