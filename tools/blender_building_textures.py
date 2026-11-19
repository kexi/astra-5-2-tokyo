"""Prepare a packed paint image and an editable UV layer for each PLATEAU building."""

import json
import tempfile
from pathlib import Path

import bpy
import numpy as np
import OpenImageIO as oiio
from blender_buildings import read_values
from blender_texture_charts import coverage, pack, photograph_charts, png
from blender_texture_original import tile_photo
from blender_texture_precision import export_positions
from blender_texture_relief import bind_infill
from blender_texture_reviews import MANUAL_IDS, PLANS, mark_screen, recipe_hash, repaint
from blender_texture_surfaces import (
    building_seed,
    classify_photo,
    donor_patch,
    plane_coords,
    surface_chart,
)

VERSION = 1
PAINT_UV = "BuildingPaint"
HOSHINOYA = "13101-bldg-1826"
HOSHINOYA_SOURCE = "https://hoshinoresorts.com/ja/hotels/hoshinoyatokyo/sp/story2/"


def geometry(obj):
    mesh = obj.data
    loops = read_values(mesh.loops, "vertex_index", np.int32).reshape(-1, 3)
    coords = read_values(mesh.vertices, "co", np.float32, 3)
    matrix = np.asarray(obj.matrix_world, dtype=np.float64)
    vertices = coords @ matrix[:3, :3].T + matrix[:3, 3]
    xyz = vertices[loops]
    corner = read_values(mesh.corner_normals, "vector", np.float32, 3) @ np.linalg.inv(
        matrix[:3, :3]
    )
    corner /= np.maximum(1e-20, np.linalg.norm(corner, axis=1, keepdims=True))
    cross = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    area = np.linalg.norm(cross, axis=1)
    normals = cross / np.maximum(1e-15, area[:, None])
    return xyz, corner.reshape(-1, 3, 3).astype(np.float32), normals, area * 0.5


def links(material):
    source = None
    if material:
        tree = material.node_tree
        bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
        base = bsdf.inputs["Base Color"]
        source = base.links[0].from_node if base.is_linked else None
        if source and source.type == "TEX_IMAGE":
            return source.image
    return None


def pixels(image, cache):
    if image not in cache:
        w, h = image.size
        values = np.empty((h, w, 4), dtype=np.float32)
        image.pixels.foreach_get(values.ravel())
        cache[image] = np.rint(np.clip(values[:, :, :3], 0, 1) * 255).astype(np.uint8)
    return cache[image]


def unpainted_color(mesh, slot, ids, color_layer):
    material = mesh.materials[slot]
    principled = (
        next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        if material
        else None
    )
    base = principled.inputs["Base Color"] if principled else None
    if base and base.is_linked and color_layer is not None:
        values = color_layer[ids].reshape(-1, 4)[:, :3]
        return np.clip(np.median(values, axis=0) * 255, 20, 226)
    linear = np.array(base.default_value[:3] if base else (0.6, 0.6, 0.6))
    srgb = np.where(
        linear <= 0.0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - 0.055
    )
    return np.clip(255 * srgb, 20, 222)


def apply_ortho(chart, xyz, adopted):
    from blender_roof_ortho import sample_roof

    ids, tex, base = chart
    # Recover the ground location of each chart texel from its measured planar
    # projection. Only sample pixels belonging to upward-facing roof footprints.
    h, w = base.shape[:2]
    v = tex[:, 1:] - tex[:, :1]
    i = np.argmax(abs(np.linalg.det(v)))
    if abs(np.linalg.det(v[i])) < 1e-7:
        return
    mask = coverage(tex, w, h)
    yy, xx = np.nonzero(mask)
    transform = np.linalg.solve(v[i], xyz[ids[i], 1:, :2] - xyz[ids[i], :1, :2])
    world = (
        xyz[ids[i], 0, :2]
        + (np.stack((xx + 0.5, yy + 0.5), axis=-1) - tex[i, 0]) @ transform
    )
    ortho, valid = sample_roof(world[:, 0], world[:, 1])
    base[yy[valid], xx[valid]] = ortho[valid]
    # A triangle-level flag also covers tiny neighbouring roofs sharing a pixel.
    _, ok = sample_roof(xyz[ids, :, 0].mean(1), xyz[ids, :, 1].mean(1))
    adopted[ids] = ok


def screen_chart(xy, ids):
    coords = xy[ids]
    lo = coords.min((0, 1))
    span = np.maximum(coords.max((0, 1)) - lo, 0.4)
    dims = np.clip(np.ceil(span * 8).astype(int), 48, 800)
    w, h = dims
    yy, xx = np.mgrid[:h, :w]
    x = lo[0] + (xx + 0.5) * span[0] / w
    z = lo[1] + (yy + 0.5) * span[1] / h
    phase = x / 1.28
    lines = np.minimum.reduce(
        [
            abs(np.sin(np.pi * (phase + z / 0.74))),
            abs(np.sin(np.pi * (phase - z / 0.74))),
            abs(np.sin(np.pi * (2 * phase))),
        ]
    )
    screen = np.clip(1 - lines / 0.35, 0, 1)
    course = np.mod(z, span[1] / 17) < 0.16
    panels = np.mod(x, 3.25) < 0.105
    ground = np.array([63, 74, 77.0])
    floor = (z - lo[1]) / (span[1] / 17)
    vision = np.clip(np.sin(np.pi * (floor % 1)) * 3, 0, 1)
    reflection = 4 * np.sin(phase * 0.42 + z * 0.12) + 4 * np.cos(z * 0.24)
    spandrel = 15 * (1 - vision)
    light = reflection + 6 * np.sin(phase * 0.72) - spandrel
    color = ground + (light - screen * 27 - 9 * course - 8 * panels)[:, :, None]
    return (
        ids,
        (coords - lo) / span * (dims - 1) + 0.5,
        color.clip(0, 255).astype(np.uint8),
    )


def assemble(obj, cache, original):
    mesh = obj.data
    xyz, corner, normals, area = geometry(obj)
    layers = mesh.uv_layers
    src = (
        read_values(layers.active.uv, "vector", np.float32, 2).reshape(-1, 3, 2)
        if layers.active
        else np.zeros((len(area), 3, 2), np.float32)
    )
    slots = read_values(mesh.polygons, "material_index", np.int32).ravel()
    uv_wall, uv_roof = plane_coords(xyz, normals, area)
    classified = np.where(
        normals[:, 2] > 0.45, 2, np.where(normals[:, 2] < -0.65, 3, 1)
    ).astype("u1")
    reason = np.zeros(len(area), "u1")
    ortho = np.zeros(len(area), bool)
    charts = []
    photos = {}
    layer = mesh.color_attributes.active_color
    colors = (
        read_values(layer.data, "color_srgb", np.float32, 4).reshape(-1, 3, 4)
        if layer and layer.domain == "CORNER"
        else None
    )
    materials = np.unique(slots)
    for slot in materials:
        image = links(mesh.materials[slot])
        if image:
            if original is not None and "repairedFacadeTriangles" not in obj:
                cache[image] = original
            photos.setdefault(image, []).append(slot)
    special = obj["id"] == HOSHINOYA
    reviewed = bool(obj.get("repairedFacadeTriangles"))
    old_repair = np.zeros(len(area), bool)
    for image, photo_slots in photos.items():
        ids = np.flatnonzero(np.isin(slots, photo_slots))
        classified[ids] = 0
        # Reviewed images are the already corrected baselines, not the city tile.
        if reviewed:
            old_repair[ids] = image != next(iter(photos))
        else:
            reason[ids] = classify_photo(
                pixels(image, cache), src, ids, normals, area, uv_wall
            )
        if special:
            reason[ids[abs(normals[ids, 2]) < 0.3]] = 3
        classified[ids[reason[ids] > 0]] = 4
        keep = ids[classified[ids] == 0]
        if len(keep):
            charts.extend(photograph_charts(pixels(image, cache), src, keep))
    donor = None
    if np.any(classified == 4) and not special:
        for image, ss in photos.items():
            healthy = (
                (classified == 0) & (abs(normals[:, 2]) < 0.22) & np.isin(slots, ss)
            )
            patch = donor_patch(pixels(image, cache), src, uv_wall, healthy)
            if patch is not None and (donor is None or patch[0] > donor[0]):
                donor = patch
    seed = building_seed(obj["id"])
    for slot in materials:
        for kind in (1, 2, 3, 4):
            ids = np.flatnonzero((slots == slot) & (classified == kind))
            if not len(ids):
                continue
            if kind == 4 and special:
                chart = screen_chart(uv_wall, ids)
            else:
                tint = unpainted_color(mesh, slot, ids, colors)
                if kind == 3:
                    tint *= 0.77
                chart = surface_chart(
                    uv_wall if kind in (1, 4) else uv_roof,
                    ids,
                    tint,
                    kind,
                    seed,
                    donor if kind == 4 else None,
                )
                if kind == 2:
                    apply_ortho(chart, xyz, ortho)
            charts.append(chart)
    atlas, dest = pack(charts, len(area))
    return (
        atlas,
        dest,
        classified,
        reason,
        ortho,
        old_repair,
        export_positions(xyz, ortho),
        corner,
        donor is not None,
    )


def attach(obj, image, dest, kind, reason, ortho, previous, donor):
    mesh = obj.data
    layer = mesh.uv_layers.new(name=PAINT_UV, do_init=False)
    layer.uv.foreach_set("vector", dest.ravel())
    mesh.uv_layers.active = layer
    layer.active_render = True
    slots = read_values(mesh.polygons, "material_index", np.int32).ravel()
    for slot in np.unique(slots):
        original = mesh.materials[slot]
        if original:
            mat = original.copy()
        else:
            mat = bpy.data.materials.new(obj["id"])
            mat.use_nodes = True
        mat.name = f"{obj['id']} · {slot}"
        tree = mat.node_tree
        surface = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
        base = surface.inputs["Base Color"]
        node = base.links[0].from_node if base.is_linked else None
        if node is None or node.type != "TEX_IMAGE":
            node = tree.nodes.new("ShaderNodeTexImage")
            node.location = (surface.location.x - 300, surface.location.y)
            tree.links.new(node.outputs["Color"], base)
        node.image = image
        node.extension = "EXTEND"
        node.label = obj["id"]
        coord = tree.nodes.new("ShaderNodeUVMap")
        coord.uv_map = layer.name
        coord.location = (node.location.x - 180, node.location.y)
        tree.links.new(coord.outputs["UV"], node.inputs["Vector"])
        if np.any(kind[slots == slot] != 0):
            bind_infill(mat, node)
        tree.nodes.active = node
        mesh.materials[slot] = mat
    used = set(np.unique(slots))
    for slot in reversed(range(len(mesh.materials))):
        if slot not in used:
            mesh.materials.pop(index=slot)
    for name, values in (
        ("TextureKind", kind),
        ("TextureRepairReason", reason),
        ("TextureOrtho", ortho),
        ("TexturePriorRepair", previous),
    ):
        a = mesh.attributes.new(name, "INT", "FACE")
        a.data.foreach_set("value", values.astype(np.int32))
    obj["textureBuildingId"] = obj["id"]
    obj["texturePhotoFaces"] = int(np.sum(kind == 0))
    obj["textureInferredFaces"] = int(np.sum((kind > 0) & (kind < 4)))
    obj["textureRepairFaces"] = int(np.sum(kind == 4))
    obj["texturePriorRepairFaces"] = int(previous.sum())
    obj["textureRepairPhotoFaces"] = int(np.sum(kind == 4)) if donor else 0
    obj["textureOrthoFaces"] = int(ortho.sum())
    if obj["id"] == HOSHINOYA:
        obj["textureDesignReference"] = HOSHINOYA_SOURCE
    obj["textureVersion"] = VERSION


def packed_image(atlas, obj, tile, temp):
    identifier = obj["id"]
    # Keep small or individually hand-painted atlases lossless. Full-resolution
    # city photographs can exceed the size of single-file release assets.
    jpeg = (
        atlas.shape[0] * atlas.shape[1] > 26000
        and "repairedFacadeTriangles" not in obj
        and (obj.get("textureVersion") != VERSION or identifier not in MANUAL_IDS)
    )
    if jpeg:
        path = Path(temp) / (identifier + ".jpg")
        spec = oiio.ImageSpec(atlas.shape[1], atlas.shape[0], 3, oiio.UINT8)
        spec.attribute(
            "CompressionQuality", 97 if obj.get("textureVersion") == VERSION else 94
        )
        spec.attribute("jpeg:subsampling", "4:4:4")
        writer = oiio.ImageOutput.create(str(path))
        if not writer.open(str(path), spec) or not writer.write_image(
            np.ascontiguousarray(atlas[::-1])
        ):
            raise OSError(f"Cannot encode {identifier}: {writer.geterror()}")
        writer.close()
        encoded = path.read_bytes()
        identity = identifier.encode("ascii")
        encoded = (
            encoded[:2]
            + b"\xff\xfe"
            + (len(identity) + 2).to_bytes(2, "big")
            + identity
            + encoded[2:]
        )
        path.write_bytes(encoded)
    else:
        path = Path(temp) / (identifier + ".png")
        encoded = png(atlas, identifier)
        path.write_bytes(encoded)
    image = bpy.data.images.load(str(path), check_existing=False)
    image.name = identifier
    image.colorspace_settings.name = "sRGB"
    image.pack(data=encoded, data_len=len(encoded))
    image.filepath = f"//building-textures/{tile}/{path.name}"
    return image, encoded, jpeg


def restore_export(obj, out, data):
    mesh = obj.data
    image = links(mesh.materials[mesh.polygons[0].material_index])
    out.write_bytes(png(pixels(image, {}), obj["id"]))
    pos, normals, _, _ = geometry(obj)
    uv = read_values(mesh.uv_layers[PAINT_UV].uv, "vector", np.float32, 2).reshape(
        -1, 3, 2
    )
    attrs = {}
    for key, label, dtype in (
        ("kind", "TextureKind", "u1"),
        ("repairReason", "TextureRepairReason", "u1"),
        ("priorRepair", "TexturePriorRepair", bool),
        ("ortho", "TextureOrtho", bool),
    ):
        attrs[key] = (
            read_values(mesh.attributes[label].data, "value", np.int32)
            .ravel()
            .astype(dtype)
        )
    np.savez_compressed(
        data,
        positions=export_positions(pos, attrs["ortho"]),
        normals=normals,
        uv=uv,
        **attrs,
    )
    image.buffers_free()


def refine_object(obj, temp, export_dir, prepared_dir=None):
    identifier = obj["id"]
    fingerprint = recipe_hash(identifier)
    if obj.get("textureReviewFingerprint") == fingerprint:
        mark_screen(obj)
        return 0
    if obj.get("textureReviewRevision"):
        raise ValueError(
            f"{identifier}: reviewed recipe changed; rebuild from the unreviewed dedicated first draft or the original building mesh"
        )
    if obj.get("textureVersion") != VERSION:
        raise ValueError(f"Build the dedicated texture for {identifier} before review")
    mesh = obj.data
    old = links(mesh.materials[mesh.polygons[0].material_index])
    src = mesh.uv_layers[PAINT_UV]
    uv = read_values(src.uv, "vector", np.float32, 2).reshape(-1, 3, 2)
    kind = read_values(mesh.attributes["TextureKind"].data, "value", np.int32).ravel()
    xyz, _, _, _ = geometry(obj)
    inputs = (identifier, pixels(old, {}), uv, xyz.astype("f4"), kind)
    quality = {}
    if prepared_dir:
        from blender_texture_precompute import load_result

        path = Path(prepared_dir) / obj["tile"] / f"{identifier}.npz"
        atlas, dest, reviewed_reasons = load_result(path, *inputs, diagnostics=quality)
    else:
        atlas, dest, reviewed_reasons = repaint(*inputs, diagnostics=quality)
    if quality:
        obj["textureScreenQuality"] = json.dumps(
            quality, allow_nan=False, separators=(",", ":")
        )
    touched = reviewed_reasons != 0
    if not touched.any():
        obj["textureReviewRevision"] = 1
        obj["textureReviewFingerprint"] = fingerprint
        mark_screen(obj)
        old.buffers_free()
        return 0
    image, _, _ = packed_image(atlas, obj, obj["tile"], temp)
    slots = read_values(mesh.polygons, "material_index", np.int32).ravel()
    for slot in np.unique(slots[touched]):
        if not np.any(kind[slots == slot] != 0):
            mat = mesh.materials[slot]
            node = next(
                n
                for n in mat.node_tree.nodes
                if n.type == "TEX_IMAGE" and n.image == old
            )
            bind_infill(mat, node)
    # Most images are referenced only by this building. Scanning the whole
    # city for each one makes the last pass quadratic in the number of IDs.
    for mat in mesh.materials:
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image == old:
                node.image = image
    if old.users:
        old.user_remap(image)
    bpy.data.images.remove(old, do_unlink=old.users > 0)
    image.name = identifier
    src.uv.foreach_set("vector", dest.ravel())
    kind[touched] = 4
    mesh.attributes["TextureKind"].data.foreach_set("value", kind)
    reasons = read_values(
        mesh.attributes["TextureRepairReason"].data, "value", np.int32
    ).ravel()
    reasons[touched] = reviewed_reasons[touched]
    mesh.attributes["TextureRepairReason"].data.foreach_set("value", reasons)
    count = int(touched.sum())
    obj["texturePhotoFaces"] = int(np.sum(kind == 0))
    obj["textureRepairFaces"] = int(np.sum(kind == 4))
    obj["textureRepairPhotoFaces"] += int(
        np.isin(reviewed_reasons, [5, 7, 8, 10]).sum()
    )
    obj["textureReviewFaces"] = count
    mark_screen(obj)
    if export_dir:
        target = Path(export_dir) / obj["tile"] / identifier
        target.parent.mkdir(parents=True, exist_ok=True)
        restore_export(obj, target.with_suffix(".png"), target.with_suffix(".npz"))
    image.buffers_free()
    obj["textureReviewRevision"] = 1
    obj["textureReviewFingerprint"] = fingerprint
    return count


def refine_buildings(identifiers=None, export_dir=None):
    """Apply reviewed crops to selected first-draft v2 buildings in place."""
    wanted = set(PLANS if identifiers is None else identifiers)
    if identifiers is None:
        stamp_screen()
    if unknown := wanted.difference(PLANS):
        raise ValueError(f"No reviewed mask for {sorted(unknown)}")
    objects = {o["id"]: o for o in bpy.data.objects if o.get("id") in wanted}
    if missing := wanted.difference(objects):
        raise ValueError(f"Missing buildings {sorted(missing)}")
    with tempfile.TemporaryDirectory(prefix="building-review-") as temp:
        return {
            key: refine_object(objects[key], temp, export_dir) for key in sorted(wanted)
        }


def stamp_screen():
    count = 0
    for obj in bpy.data.objects:
        if obj.get("textureVersion") == VERSION and obj.get("id"):
            mark_screen(obj)
            count += 1
    return count


def texture_tiles(tile_ids, export_dir=None, prepared_dir=None):
    stats = {"tiles": 0, "buildings": 0, "bytes": 0, "repaired": 0}
    with tempfile.TemporaryDirectory(prefix="building-paint-") as temp:
        root = Path(export_dir or temp)
        for tile in tile_ids:
            cache = {}
            group = bpy.data.collections[tile]
            original = (
                tile_photo(tile)
                if any(o.get("textureVersion") != VERSION for o in group.objects)
                else None
            )
            folder = root / tile
            folder.mkdir(exist_ok=True, parents=True)
            for obj in group.objects:
                identifier = obj["id"]
                mark_screen(obj)
                out = folder / f"{identifier}.png"
                data = folder / f"{identifier}.npz"
                if obj.get("textureVersion") == VERSION:
                    if identifier in PLANS:
                        refine_object(obj, temp, export_dir, prepared_dir)
                    if export_dir and not (out.exists() and data.exists()):
                        restore_export(obj, out, data)
                    continue
                atlas, uv, kind, reason, ortho, previous, position, normal, donor = (
                    assemble(obj, cache, original)
                )
                image, encoded, jpeg = packed_image(atlas, obj, tile, temp)
                if export_dir:
                    # Use Blender's own decoded packed pixels as the shared Web
                    # source even if the native editing image uses JPEG.
                    out.write_bytes(
                        png(pixels(image, {}), identifier) if jpeg else encoded
                    )
                    np.savez_compressed(
                        data,
                        positions=position,
                        normals=normal,
                        uv=uv,
                        kind=kind,
                        repairReason=reason,
                        ortho=ortho,
                        priorRepair=previous,
                    )
                attach(obj, image, uv, kind, reason, ortho, previous, donor)
                image.buffers_free()
                if identifier in PLANS:
                    refine_object(obj, temp, export_dir, prepared_dir)
                stats["buildings"] += 1
                stats["bytes"] += len(encoded)
                stats["repaired"] += int(np.sum(kind == 4))
            stats["tiles"] += 1
    return stats
