# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = []
#
# [tool.blender]
# version = "5.2"
# ///
"""Render every saved building at eight bearings and from above for texture review.

blender -b output/tokyo-station.blend --python-exit-code 1 \
  -P tools/review-building-textures.py -- /tmp/tokyo-building-review
"""

import argparse
import csv
import ctypes as c
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from zipfile import BadZipFile

import bpy
import numpy as np

HERE = Path(__file__).resolve().parent
BUILDING_INDEX = HERE.parent / "review/building-textures.csv"
BEARINGS = [0, 45, 90, 135, 180, 225, 270, 315, "俯瞰"]
BACKGROUND = np.uint32(0xFFFFFFFF)


class Paint(c.Structure):
    _fields_ = [
        ("pixels", c.c_void_p),
        ("width", c.c_int),
        ("height", c.c_int),
        ("repeat", c.c_int),
        ("plain", c.c_uint8 * 3),
    ]


@dataclass
class Building:
    xyz: np.ndarray
    uv: np.ndarray
    slots: np.ndarray
    paints: list[Paint]
    images: list[np.ndarray]
    geometry: np.ndarray
    provenance: np.ndarray
    center: np.ndarray


def native_library(directory):
    source = HERE / "review-building-raster.cpp"
    target = Path(directory) / (
        "raster.dylib" if sys.platform == "darwin" else "raster.so"
    )
    subprocess.run(
        [
            os.getenv("CXX", "c++"),
            "-std=c++17",
            "-O3",
            "-fPIC",
            "-shared",
            str(source),
            "-o",
            str(target),
            "-lz",
        ],
        check=True,
    )
    native = c.CDLL(str(target))
    native.render_building.argtypes = [
        c.c_int,
        c.c_void_p,
        c.c_void_p,
        c.c_void_p,
        c.POINTER(Paint),
        c.c_int,
        c.c_int,
        c.c_void_p,
        c.c_void_p,
    ]
    native.render_building.restype = None
    native.atlas_usage.argtypes = [
        c.c_int,
        c.c_void_p,
        c.c_void_p,
        c.c_int,
        c.c_int,
        c.c_void_p,
    ]
    native.atlas_usage.restype = None
    native.save_review_png.argtypes = [c.c_char_p, c.c_int, c.c_int, c.c_void_p]
    native.save_review_png.restype = c.c_int
    return native


def array(collection, prop, width, dtype=np.float32):
    values = np.empty((len(collection), width), dtype=dtype)
    collection.foreach_get(prop, values.ravel())
    return values


def quantize(value):
    return tuple(round(255 * np.clip(ch, 0, 1)) for ch in value)


def solid_color(color):
    return quantize(
        [
            12.92 * ch if ch < 0.0031308 else 1.055 * ch ** (1 / 2.4) - 0.055
            for ch in color
        ]
    )


@lru_cache(maxsize=24)
def image_pixels(image):
    if not image.packed_file or not all(image.size):
        raise ValueError(f"画像が埋め込まれていません: {image.name}")
    return pixels(image)


def pixels(image):
    if image.is_float or image.colorspace_settings.name != "sRGB":
        raise ValueError(f"RGB8 sRGBを想定した画像: {image.name}")
    rgb = np.empty((image.size[1], image.size[0], 4), dtype=np.float32)
    image.pixels.foreach_get(rgb.ravel())
    return np.ascontiguousarray(
        np.rint(np.clip(rgb[..., :3], 0, 1) * 255), dtype=np.uint8
    )


def material_input(material, strict):
    if material is None or not material.node_tree:
        if strict:
            raise ValueError("Base Color材質がありません")
        return None, None, False, (190, 198, 207)
    shader = next(
        (node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"),
        None,
    )
    if shader is None:
        if strict:
            raise ValueError(f"Principled BSDFがありません: {material.name}")
        return None, None, False, solid_color(material.diffuse_color[:3])
    base = shader.inputs["Base Color"]
    node = base.links[0].from_node if base.is_linked else None
    if node is None or node.type != "TEX_IMAGE" or not node.image:
        if strict:
            raise ValueError(
                f"Base Colorに専用画像が接続されていません: {material.name}"
            )
        if node:
            node = next(
                (
                    candidate
                    for candidate in material.node_tree.nodes
                    if candidate.type == "TEX_IMAGE" and candidate.image
                ),
                None,
            )
        if node is None:
            return None, None, False, solid_color(base.default_value[:3])
    vector = node.inputs["Vector"]
    uv = None
    if vector.is_linked:
        source = vector.links[0].from_node
        if source.type == "UVMAP":
            uv = source.uv_map
        elif source.type != "TEX_COORD" or vector.links[0].from_socket.name != "UV":
            raise ValueError(f"未対応の画像座標入力: {material.name}")
    return node.image, uv, node.extension == "REPEAT", (0, 0, 0)


def extract(obj, strict):
    mesh = obj.data
    mesh.calc_loop_triangles()
    triangles = mesh.loop_triangles
    corners = array(triangles, "loops", 3, np.int32)
    vertices = array(mesh.loops, "vertex_index", 1, np.int32).ravel()[corners]
    if len(triangles) == 0:
        raise ValueError("三角形がありません")
    source_vertices = array(mesh.vertices, "co", 3)
    # A standalone .blend library can have no active scene, so matrix_world is
    # not evaluated there. These buildings are unparented and retain matrix_basis.
    if obj.parent:
        raise ValueError(
            f"親を持つ建物の変換は制作出力NPZで確認してください: {obj.name}"
        )
    matrix = np.asarray(obj.matrix_basis, dtype=np.float32)
    rotation = matrix[:3, :3]
    point_cloud = source_vertices @ rotation.T
    offset = (point_cloud.min(axis=0) + point_cloud.max(axis=0)) * 0.5
    point_cloud -= offset
    xyz = np.ascontiguousarray(point_cloud[vertices])
    slots = array(triangles, "material_index", 1, np.int32).ravel()
    expected = sorted(set(slots))
    atlas = set()
    paints = {}
    buffers = {}
    uv = np.zeros((*corners.shape, 2), dtype=np.float32)
    for slot in expected:
        material = (
            obj.material_slots[slot].material
            if slot < len(obj.material_slots)
            else None
        )
        image, map_name, repeat, plain = material_input(material, strict)
        if image is None:
            paints[slot] = Paint(None, 0, 0, 0, plain)
            continue
        layer = (
            mesh.uv_layers.get(map_name)
            if map_name
            else next(
                (value for value in mesh.uv_layers if value.active_render),
                mesh.uv_layers.active,
            )
        )
        if layer is None:
            raise ValueError(f"画像{image.name}に対応するUVがありません")
        uvs = array(layer.uv, "vector", 2)
        uv[slots == slot] = uvs[corners[slots == slot]]
        buffers[image] = rgb = image_pixels(image)
        height, width = rgb.shape[:2]
        paints[slot] = Paint(rgb.ctypes.data, width, height, int(repeat), plain)
        atlas.add(image)
    if strict and (len(atlas) != 1 or next(iter(atlas)).name != obj["id"]):
        raise ValueError("棟IDと同名の専用画像が1枚必要です")
    if not np.isfinite(xyz).all() or not np.isfinite(uv).all():
        raise ValueError("位置またはUVに非有限値があります")
    if strict and (uv.min() < -0.00001 or uv.max() > 1.00001):
        raise ValueError("UVが専用アトラスからはみ出しています")

    kind = mesh.attributes.get("TextureKind")
    if strict and (
        obj.get("textureVersion") != 1
        or kind is None
        or kind.domain != "FACE"
        or obj.get("textureBuildingId") != obj["id"]
    ):
        raise ValueError("棟別アトラスと面由来の属性が不足しています")
    if kind:
        polygons = array(triangles, "polygon_index", 1, np.int32).ravel()
        provenance = array(kind.data, "value", 1, np.int32).ravel()[polygons]
        if not np.isin(provenance, (0, 1, 2, 3, 4)).all():
            raise ValueError("TextureKindに未定義の値があります")
        if strict and (
            obj.get("texturePhotoFaces") != np.count_nonzero(provenance == 0)
            or obj.get("textureInferredFaces")
            != np.count_nonzero((provenance > 0) & (provenance < 4))
        ):
            raise ValueError("写真面・推定仕上げの面属性と集計が一致しません")
    else:
        provenance = np.where(
            np.isin(slots, [slot for slot, paint in paints.items() if paint.pixels]),
            0,
            1,
        )
    normalized_slots = np.searchsorted(expected, slots).astype(np.int32)
    return Building(
        xyz,
        uv,
        normalized_slots,
        list(paints.values()),
        list(buffers.values()),
        slope(xyz),
        provenance,
        offset + matrix[:3, 3],
    )


def slope(xyz):
    surface = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    lengths = np.linalg.norm(surface, axis=1)
    vertical = np.divide(
        surface[:, 2], lengths, out=np.zeros_like(lengths), where=lengths > 0
    )
    orientation = np.zeros(len(xyz), dtype=np.uint8)
    orientation[np.abs(vertical) <= 0.35] = 1  # Wall
    orientation[vertical >= 0.65] = 2  # Roof
    orientation[lengths == 0] = 0
    return orientation


def extract_export(path, official):
    """Read the exact triangles, UVs, and sRGB image exported by the Blender builder."""
    packed = path.read_bytes()
    with np.load(io.BytesIO(packed), allow_pickle=False) as source:
        xyz = np.ascontiguousarray(source["positions"], dtype=np.float32)
        normals = np.asarray(source["normals"], dtype=np.float32)
        uv = np.ascontiguousarray(source["uv"], dtype=np.float32)
        kind = np.asarray(source["kind"])
    original_photos = int(official["image_triangles"])
    count = original_photos + int(official["other_triangles"])
    if (
        xyz.shape != (count, 3, 3)
        or normals.shape != xyz.shape
        or uv.shape != (count, 3, 2)
        or kind.shape != (count,)
        or not all(np.isfinite(values).all() for values in (xyz, normals, uv))
        or not np.isin(kind, (0, 1, 2, 3, 4)).all()
        or uv.min() < -0.00001
        or uv.max() > 1.00001
    ):
        raise ValueError("制作出力の三角形・UV・法線・由来属性が監査仕様に一致しません")
    if original_photos != np.count_nonzero((kind == 0) | (kind == 4)):
        raise ValueError("元写真に対応する三角形数が監査表と一致しません")
    flat = xyz.reshape(-1, 3)
    center = (flat.min(axis=0) + flat.max(axis=0)) * 0.5
    xyz -= center
    source_png = path.with_suffix(".png")
    image_hash = file_hash(source_png)
    image = bpy.data.images.load(str(source_png), check_existing=False)
    try:
        if not all(image.size):
            raise ValueError(f"画像を復号できません: {source_png}")
        rgb = pixels(image)
    finally:
        bpy.data.images.remove(image)
    if file_hash(source_png) != image_hash:
        raise ValueError(f"描画中に元画像が更新されました: {source_png}")
    height, width = rgb.shape[:2]
    paint = Paint(rgb.ctypes.data, width, height, 0, (0, 0, 0))
    result = Building(
        xyz,
        uv,
        np.zeros(count, dtype=np.int32),
        [paint],
        [rgb],
        slope(xyz),
        kind,
        center,
    )
    return result, (hashlib.sha256(packed).hexdigest(), image_hash)


def verify_export(export, obj):
    original = extract(obj, True)
    if (
        original.xyz.shape != export.xyz.shape
        or not np.array_equal(original.uv, export.uv)
        or not np.array_equal(original.provenance, export.provenance)
        or not np.array_equal(original.images[0], export.images[0])
    ):
        raise ValueError(
            f"{obj.name}: Blender実ファイルと出力NPZ/PNGのUV・色・面由来が一致しません"
        )
    position = original.xyz - export.xyz + (original.center - export.center)
    error = float(np.linalg.norm(position, axis=2).max())
    if error > 0.001:
        raise ValueError(
            f"{obj.name}: Blender実ファイルと制作出力の位置差 {error:.6g} m"
        )
    return error


def overlap(native, uv, slots, paints, geometry, provenance):
    groups = defaultdict(list)
    for material, paint in enumerate(paints):
        if paint.pixels:
            groups[(paint.pixels, paint.width, paint.height)].append(material)
    measurements = []
    for (_, full_width, full_height), materials in groups.items():
        width, height = min(full_width, 192), min(full_height, 192)
        coverage = np.zeros((height, width), dtype=np.uint8)
        regions = np.where(
            np.isin(slots, materials) & (provenance == 0), geometry, 0
        ).astype(np.uint8)
        native.atlas_usage(
            len(uv),
            uv.ctypes.data,
            regions.ctypes.data,
            width,
            height,
            coverage.ctypes.data,
        )
        both = coverage == 3
        # Ignore one texel at boundaries between correctly packed islands.
        overlap_core = (
            both[1:-1, 1:-1]
            & both[1:-1, :-2]
            & both[1:-1, 2:]
            & both[:-2, 1:-1]
            & both[2:, 1:-1]
        )
        walls = np.count_nonzero(coverage & 1)
        measurements.append((int(overlap_core.sum()), int(walls)))
    return sum(pair[0] for pair in measurements) / max(
        1, sum(pair[1] for pair in measurements)
    )


def patches(images, masks):
    """Describe wall and roof patches using color and directional luminance variation."""
    side = images.shape[1]
    steps = 6
    cell = side // steps
    end = steps * cell
    block = (
        images[:, :end, :end]
        .reshape(9, steps, cell, steps, cell, 3)
        .transpose(0, 1, 3, 2, 4, 5)
    )
    flags = (
        masks[:, :end, :end]
        .reshape(9, steps, cell, steps, cell)
        .transpose(0, 1, 3, 2, 4)
    )
    coverage = [(flags == label).mean(axis=(3, 4)) for label in (1, 2)]
    # Only predominantly one-surface blocks enter the comparison. Neighboring
    # facade and roof blocks can legitimately share the same colors.
    walls, roofs = (value >= 0.80 for value in coverage)
    chroma = block.mean(axis=(3, 4), dtype=np.float32) / 255
    light = (block[..., 0] * 0.22 + block[..., 1] * 0.70 + block[..., 2] * 0.08).astype(
        np.float32
    ) / 255
    contrast = light.std(axis=(3, 4))
    vertical = np.abs(np.diff(light, axis=3)).mean(axis=(3, 4))
    horizontal = np.abs(np.diff(light, axis=4)).mean(axis=(3, 4))
    diagonal = np.abs(light[..., 1:, 1:] - light[..., :-1, :-1]).mean(axis=(3, 4))
    structure = np.stack((contrast, vertical, horizontal, diagonal), axis=-1)
    descriptors = np.concatenate((chroma, structure * 1.75), axis=-1)
    scores = np.zeros((9, steps, steps), dtype=np.float32)
    if np.any(walls) and np.any(roofs):
        distances = np.linalg.norm(
            descriptors[walls, None] - descriptors[roofs][None, :], axis=2
        )
        similarity = np.maximum(0, 1 - distances.min(axis=1) / 0.23)
        detail = np.clip((contrast[walls] - 0.026) / 0.13, 0, 1)
        irregular = np.minimum(vertical[walls], horizontal[walls]) / np.maximum(
            0.01, np.maximum(vertical[walls], horizontal[walls])
        )
        scores[walls] = similarity * (0.20 + 0.80 * detail) * (0.30 + 0.70 * irregular)
    colors = block.astype(np.int16)
    red, green, blue = (colors[..., channel] for channel in range(3))
    green_patch = (
        ((green > red + 11) & (green > blue + 12) & (green > 58)).mean(axis=(3, 4))
    ) * walls
    return walls, contrast, chroma, scores, green_patch.max(axis=(1, 2))


def enclosed_color(walls, chroma):
    """Measure an interior patch separated in at least three directions."""
    center = chroma[:, 1:-1, 1:-1]
    neighbors = np.stack(
        [
            np.linalg.norm(center - chroma[:, y, x], axis=3) * walls[:, y, x]
            for y, x in (
                (slice(None, -2), slice(1, -1)),
                (slice(2, None), slice(1, -1)),
                (slice(1, -1), slice(None, -2)),
                (slice(1, -1), slice(2, None)),
            )
        ]
    )
    boundary = np.sort(neighbors, axis=0)[1] * walls[:, 1:-1, 1:-1]
    return boundary.max(axis=(1, 2))


def screen(images, owners, geometry, provenance):
    visible = owners != BACKGROUND
    safe = np.where(visible, owners, 0)
    component = np.where(visible, geometry[safe], 0)
    photo = (provenance[safe] == 0) & visible
    painted = np.where(photo, component, 0)
    walls, contrast, chroma, similarity, green_patch = patches(images, painted)
    enclosure = enclosed_color(walls, chroma)
    pixels = images.astype(np.int16)
    r, g, b = (pixels[..., idx] for idx in range(3))
    luminance = (2 * r + 7 * g + b) / 10
    wall = photo & (component == 1)
    wall_counts = np.maximum(1, wall.sum(axis=(1, 2)))
    green = wall & (g > r + 11) & (g > b + 12) & (g > 58)
    vivid = wall & (np.max(pixels, axis=3) - np.min(pixels, axis=3) > 89)
    extreme = wall & ((luminance < 12) | (luminance > 246))
    jumps_x = np.linalg.norm(np.diff(chroma, axis=2), axis=3)
    jumps_y = np.linalg.norm(np.diff(chroma, axis=1), axis=3)
    seam_x = jumps_x * (walls[:, :, :-1] & walls[:, :, 1:])
    seam_y = jumps_y * (walls[:, :-1, :] & walls[:, 1:, :])
    rectangular = np.maximum(seam_x.max(axis=(1, 2)), seam_y.max(axis=(1, 2)))
    repeated = np.zeros(9, dtype=float)
    for axis in (1, 2):
        first = [slice(None)] * 3
        second = first.copy()
        stride = max(3, images.shape[1] // 16)
        first[axis], second[axis] = slice(None, -stride), slice(stride, None)
        aa, zz = tuple(first), tuple(second)
        pairs = wall[aa] & wall[zz]
        shifted = np.abs(luminance[aa] - luminance[zz])
        periodicity = ((shifted < 3) & pairs).sum(axis=(1, 2)) / np.maximum(
            1, pairs.sum(axis=(1, 2))
        )
        repeated = np.maximum(
            repeated, periodicity * np.minimum(1, contrast.max(axis=(1, 2)) * 10)
        )
    per_view = []
    for view, bearing in enumerate(BEARINGS):
        per_view.append(
            {
                "bearing": bearing,
                "visible_pixels": int(visible[view].sum()),
                "photo_wall_pixels": int(wall[view].sum()),
                "green_wall": round(float(green[view].sum() / wall_counts[view]), 5),
                "green_patch": round(float(green_patch[view]), 5),
                "vivid_wall": round(float(vivid[view].sum() / wall_counts[view]), 5),
                "extreme_wall": round(
                    float(extreme[view].sum() / wall_counts[view]), 5
                ),
                "color_seam": round(float(rectangular[view]), 5),
                "interior_border": round(float(enclosure[view]), 5),
                "repetition": round(float(repeated[view]), 5),
                "roof_resemblance": round(float(similarity[view].max()), 5),
            }
        )
    return per_view


def contact_sheet(images):
    side = images.shape[1]
    mosaic = np.full((side * 3 + 4, side * 3 + 4, 3), (86, 98, 108), dtype=np.uint8)
    for index, frame in enumerate(images):
        row, col = divmod(index, 3)
        top, left = row * (side + 2), col * (side + 2)
        mosaic[top : top + side, left : left + side] = frame
    return mosaic


def rank(overlap_fraction, views):
    eligible = [view for view in views if view["photo_wall_pixels"] >= 48]
    if not eligible:
        return 0, None, []
    priority = lambda view: max(
        1.65 * view["green_wall"],
        0.9 * view["green_patch"],
        1.6 * view["interior_border"],
        0.8 * view["vivid_wall"],
        0.75 * view["color_seam"],
        0.85 * view["roof_resemblance"],
        0.45 * view["extreme_wall"],
        0.15 * view["repetition"],
    )
    focus = max(eligible, key=priority)
    score = max(priority(focus), min(1, 7 * overlap_fraction))
    reasons = []
    if overlap_fraction >= 0.015:
        reasons.append("屋根・壁のUV重複")
    for metric, limit, label in (
        ("green_wall", 0.06, "壁面の緑"),
        ("green_patch", 0.20, "壁面の局所的な緑"),
        ("interior_border", 0.12, "壁の中で囲まれた色差"),
        ("vivid_wall", 0.08, "壁面の高彩度部分"),
        ("color_seam", 0.18, "壁面の急な色差"),
        ("roof_resemblance", 0.20, "壁と屋根の模様が近い"),
        ("extreme_wall", 0.25, "壁面の極端な明暗"),
        ("repetition", 0.55, "壁面の反復"),
    ):
        if max(view[metric] for view in eligible) >= limit:
            reasons.append(label)
    return round(float(score), 5), focus["bearing"], reasons


def render(data, side, factor, native, root, relative):
    xyz, uv, slots = data.xyz, data.uv, data.slots
    paints, geometry, provenance = data.paints, data.geometry, data.provenance
    images = np.empty((9, side, side, 3), dtype=np.uint8)
    owners = np.empty((9, side, side), dtype=np.uint32)
    native.render_building(
        len(xyz),
        xyz.ctypes.data,
        uv.ctypes.data,
        slots.ctypes.data,
        (Paint * len(paints))(*paints),
        side,
        factor,
        images.ctypes.data,
        owners.ctypes.data,
    )
    uv_overlap = overlap(native, uv, slots, paints, geometry, provenance)
    views = screen(images, owners, geometry, provenance)
    score, focus, reasons = rank(uv_overlap, views)
    if min(view["visible_pixels"] for view in views) <= 7:
        reasons.append("輪郭がほとんど映らない方位")
    output = root / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    mosaic = contact_sheet(images)
    if not native.save_review_png(
        os.fsencode(output), mosaic.shape[1], mosaic.shape[0], mosaic.ctypes.data
    ):
        raise OSError(f"PNGを書き出せません: {output}")
    return {
        "faces": len(xyz),
        "height_m": round(float(np.ptp(xyz[..., 2])), 3),
        "supersample_axis": factor,
        "paint_kinds": dict(Counter(map(int, provenance))),
        "views": views,
        "score": score,
        "bearing": focus,
        "reasons": reasons,
        "roof_wall_uv_overlap": round(uv_overlap, 5),
        "image": relative.as_posix(),
        "render_sha256": file_hash(output),
    }


def prior_renders(folder, side, raster_sha256):
    if folder is None:
        return {}
    metadata = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    if (
        metadata["mode"] != "export"
        or metadata["size_per_view_px"] != side
        or metadata.get("raster_sha256") != raster_sha256
    ):
        raise ValueError("描画方式・画素数が異なる過去の結果は再利用できません")
    with (folder / "views.jsonl").open(encoding="utf-8") as stream:
        return {row["id"]: row for row in map(json.loads, stream) if row.get("image")}


def reuse_render(row, path, relative, previous, output, factor):
    if (
        not row
        or row["image"] != relative.as_posix()
        or row["supersample_axis"] != factor
    ):
        return None
    if file_hash(path) != row.get("mesh_sha256") or file_hash(
        path.with_suffix(".png")
    ) != row.get("image_sha256"):
        return None
    original = previous / relative
    if not original.is_file() or file_hash(original) != row.get("render_sha256"):
        return None
    target = output / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(original, target)
    except OSError:
        shutil.copyfile(original, target)
    return row


def supersample(args, identifiers, identifier, height):
    if args.detail_supersample and (height >= 60 or identifier in identifiers):
        return args.detail_supersample
    return args.supersample


def run(args):
    with BUILDING_INDEX.open(encoding="utf-8") as source:
        index = {row["building_id"]: row for row in csv.DictReader(source)}
    if args.export:
        objects = {
            key: path
            for key, row in index.items()
            if (path := args.export / row["tile"] / (key + ".npz")).is_file()
        }
    else:
        objects = scene_buildings(index)
    selected = args.id or sorted(objects if args.subset else index)
    if args.limit:
        selected = selected[: args.limit]
    if (
        not args.id
        and not args.subset
        and not args.limit
        and set(objects) != set(index)
    ):
        raise ValueError(
            f"全棟を取得できません: 応答 {len(objects)} / 監査表 {len(index)}"
        )
    missing = set(selected) - objects.keys()
    if not selected or missing or len(selected) != len(set(selected)):
        raise ValueError(
            f"対象棟がありません、またはID指定が重複しています: {sorted(missing)[:8]}"
        )
    detail_ids = (
        set(args.detail_ids.read_text(encoding="utf-8").split())
        if args.detail_ids
        else set()
    )
    invalid_details = detail_ids - index.keys()
    if invalid_details:
        raise ValueError(
            f"詳細描画に不明な棟IDがあります: {sorted(invalid_details)[:8]}"
        )
    args.output.mkdir(parents=True, exist_ok=True)
    csv_path = args.output / "candidates.csv"
    rows = []
    errors = 0
    reused = 0
    renderer = (
        Path(__file__).read_bytes() + (HERE / "review-building-raster.cpp").read_bytes()
    )
    raster_sha256 = hashlib.sha256(renderer).hexdigest()
    before = prior_renders(args.reuse, args.size, raster_sha256)
    blend_max_error = 0.0
    matching = scene_buildings(index) if args.verify_blender else {}
    if args.verify_blender and not set(selected) <= matching.keys():
        raise ValueError("照合するBlender実ファイルに必要な建物がありません")
    started = time.monotonic()
    export_digest = hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix="review-buildings-") as scratch:
        native = native_library(scratch)
        with (args.output / "views.jsonl").open("w", encoding="utf-8") as stream:
            for count, identifier in enumerate(selected, 1):
                official = index[identifier]
                relative = Path("buildings") / official["tile"] / (identifier + ".png")
                record = {
                    "id": identifier,
                    "name": official["name"],
                    "tile": official["tile"],
                    "lod": official["lod"],
                }
                try:
                    cached = before.get(identifier)
                    cached_factor = supersample(
                        args,
                        detail_ids,
                        identifier,
                        cached["height_m"] if cached else 0,
                    )
                    previous = reuse_render(
                        cached,
                        objects[identifier],
                        relative,
                        args.reuse,
                        args.output,
                        cached_factor,
                    )
                    if previous:
                        record.update(previous)
                        mesh_hash, image_hash = (
                            previous["mesh_sha256"],
                            previous["image_sha256"],
                        )
                        reused += 1
                    elif args.export:
                        data, (mesh_hash, image_hash) = extract_export(
                            objects[identifier], official
                        )
                        record.update(mesh_sha256=mesh_hash, image_sha256=image_hash)
                        if args.verify_blender:
                            error = verify_export(data, matching[identifier])
                            blend_max_error = max(blend_max_error, error)
                    else:
                        data = extract(objects[identifier], not args.source_materials)
                    if not previous:
                        height = round(float(np.ptp(data.xyz[..., 2])), 3)
                        factor = supersample(args, detail_ids, identifier, height)
                        record.update(
                            render(
                                data, args.size, factor, native, args.output, relative
                            )
                        )
                    if args.export:
                        export_digest.update(
                            f"{identifier}\t{mesh_hash}\t{image_hash}\n".encode()
                        )
                except (ValueError, OSError, BadZipFile) as error:
                    errors += 1
                    record.update(
                        error=str(error),
                        score=10,
                        bearing=None,
                        reasons=["描画できません"],
                        image=None,
                    )
                rows.append(record)
                stream.write(
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                if count % 500 == 0:
                    print(
                        f"描画 {count:,}/{len(selected):,} 棟、{time.monotonic() - started:.1f} 秒",
                        flush=True,
                    )
    rows.sort(key=lambda row: (-row["score"], row["id"]))
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        fields = (
            "id",
            "name",
            "tile",
            "lod",
            "height_m",
            "score",
            "bearing",
            "roof_wall_uv_overlap",
            "reasons",
            "image",
            "render_sha256",
            "error",
        )
        writer = csv.DictWriter(stream, fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row, reasons=" / ".join(row["reasons"])))
    report = {
        "source": str(args.export.resolve()) if args.export else bpy.data.filepath,
        "source_sha256": export_digest.hexdigest()
        if args.export
        else file_hash(bpy.data.filepath),
        "mode": "export"
        if args.export
        else "source"
        if args.source_materials
        else "dedicated",
        "buildings_expected": len(selected),
        "buildings_rendered": len(selected) - errors,
        "views_rendered": (len(selected) - errors) * len(BEARINGS),
        "views_with_geometry": sum(
            view["visible_pixels"] > 0 for row in rows for view in row.get("views", ())
        ),
        "bearings": BEARINGS,
        "size_per_view_px": args.size,
        "supersampling": {
            "method": "area average of regularly spaced color and depth samples",
            "buildings_by_factor": dict(
                Counter(row["supersample_axis"] for row in rows if row.get("image"))
            ),
        },
        "errors": errors,
        "raster_sha256": raster_sha256,
        "reused_views": reused * len(BEARINGS),
        "machines_flags": dict(
            Counter(reason for row in rows for reason in row["reasons"])
        ),
        "wall_seconds": round(time.monotonic() - started, 2),
    }
    if args.verify_blender:
        report["blender_sha256"] = file_hash(bpy.data.filepath)
        report["blender_max_position_error_m"] = blend_max_error
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if errors:
        raise ValueError(
            f"{errors}棟を描画できませんでした。candidates.csvを確認してください"
        )


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def scene_buildings(index):
    objects = {}
    for obj in bpy.data.objects:
        identifier = obj.get("id")
        if obj.type != "MESH" or identifier not in index:
            continue
        if obj["gml"] != index[identifier]["gml_id"]:
            raise ValueError(f"棟IDとGMLが対応しません: {obj.name}")
        if identifier in objects:
            raise ValueError(f"棟IDが重複しています: {identifier}")
        objects[identifier] = obj
    return objects


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--size", type=int, default=96, help="各方位の画像幅と高さ")
    parser.add_argument(
        "--supersample", type=int, choices=(1, 2, 4), default=1, help="XY各軸の描画倍率"
    )
    parser.add_argument(
        "--detail-supersample",
        type=int,
        choices=(2, 4),
        help="60m以上・指定棟の描画倍率",
    )
    parser.add_argument(
        "--detail-ids", type=Path, help="詳細描画に含める棟IDを改行で指定"
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--export", type=Path, help="Blender制作のNPZとPNGを入力")
    source.add_argument(
        "--source-materials",
        action="store_true",
        help="既存のタイル由来材質で性能を測定",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--reuse", type=Path, help="NPZ・PNGと描画結果のSHAが一致した実描画を再利用"
    )
    parser.add_argument(
        "--verify-blender",
        action="store_true",
        help="制作NPZ・PNGを、開いた最終Blendの実UV・画素・位置と照合",
    )
    parser.add_argument(
        "--subset",
        action="store_true",
        help="入力の部分ライブラリに含まれる棟のみを描画",
    )
    parser.add_argument(
        "--id", action="append", help="指定IDのみを描画。複数回指定可能"
    )
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    if args.verify_blender and (not args.export or not bpy.data.filepath):
        parser.error("--verify-blender には実ファイルの -b と --export が必要です")
    if args.reuse and (
        not args.export
        or args.verify_blender
        or args.reuse.resolve() == args.output.resolve()
    ):
        parser.error(
            "--reuse は --export を指定し、別の出力先へ書き出す場合に使用できます"
        )
    if args.size < 48 or args.size > 768 or (args.limit is not None and args.limit < 1):
        parser.error("画像サイズ48～768、limitは1以上を指定してください")
    if args.detail_ids and not args.detail_supersample:
        parser.error("--detail-ids には --detail-supersample が必要です")
    if args.detail_supersample and args.detail_supersample < args.supersample:
        parser.error("詳細描画倍率は通常の描画倍率以上を指定してください")
    run(args)


if __name__ == "__main__":
    main()
