# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = []
#
# [tool.blender]
# version = "5.2"
# ///
"""Model the surveyed Yaesu street trees in Blender, from each original PLATEAU batch."""

import gzip
import json
import math
import runpy
import tempfile
import zlib
from collections import defaultdict
from itertools import pairwise
from pathlib import Path
from random import Random

import bpy
import numpy as np
from mathutils import Vector

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
UP = Vector((0, 0, 1))
TAU = math.tau


class TreeMesh:
    def __init__(self, rng):
        self.rng = rng
        self.vertices = []
        self.colors = []
        self.faces = []

    def point(self, co, color):
        self.vertices.append(tuple(co))
        self.colors.append((*color, 1))
        return len(self.vertices) - 1

    def tube(self, a, b, ra, rb):
        axis = (b - a).normalized()
        u = axis.cross(Vector((0, 1, 0))).normalized()
        v = axis.cross(u)
        rings = []
        for p, radius in ((a, ra), (b, rb)):
            ring = []
            for i in range(8):
                angle = TAU * i / 8
                wiggle = 0.88 + 0.20 * math.sin(i * 2.8)
                wood = (0.134 * wiggle, 0.10 * wiggle, 0.069 * wiggle)
                ring.append(
                    self.point(
                        p + radius * (u * math.cos(angle) + v * math.sin(angle)), wood
                    )
                )
            rings.append(ring)
        self.join_rings(*rings)

    def join_rings(self, a, b):
        for i in range(len(a)):
            j = (i + 1) % len(a)
            self.faces.append((a[i], a[j], b[j], b[i]))

    def leaf(self, p, angle, length, color):
        axis = Vector(
            (math.cos(angle), math.sin(angle), self.rng.uniform(-0.75, 0.7))
        ).normalized()
        across = axis.cross(UP).normalized()
        root = self.point(p, color)
        tip = self.point(p + axis * length, color)
        center = p + 0.48 * length * axis
        fold = self.point(center + UP * (0.10 * length), tuple(c * 1.16 for c in color))
        left = self.point(center - across * length * 0.24, color)
        right = self.point(center + across * length * 0.24, color)
        self.faces.extend(
            (
                (root, left, fold),
                (fold, left, tip),
                (root, fold, right),
                (fold, tip, right),
            )
        )

    def cluster(self, center, radius, color):
        rings = []
        phase = self.rng.random() * TAU
        for row in range(4):
            theta = math.pi * (0.04 + 0.92 * row / 3)
            ring = []
            for i in range(7):
                phi = phase + TAU * (i + 0.42 * (row % 2)) / 7
                direction = Vector(
                    (
                        math.sin(theta) * math.cos(phi),
                        math.sin(theta) * math.sin(phi),
                        -math.cos(theta),
                    )
                )
                shape = self.rng.uniform(0.78, 1.17)
                point = center + Vector(
                    tuple(d * r * shape for d, r in zip(direction, radius))
                )
                tone = (0.85 + 0.10 * direction.z) * self.rng.uniform(0.80, 1.23)
                tint = tuple(c * tone for c in color)
                ring.append(self.point(point, tint))
                if 0 < row < 3 and (i + row) % 4 == 0:
                    self.leaf(
                        point,
                        phi + self.rng.uniform(-0.8, 0.8),
                        self.rng.uniform(0.085, 0.17),
                        tint,
                    )
            rings.append(ring)
        for a, b in pairwise(rings):
            self.join_rings(a, b)
        self.faces.extend((tuple(reversed(rings[0])), tuple(rings[-1])))


def model_tree(points, tile, batch, material):
    low, high = points.min(axis=0), points.max(axis=0)
    foot = (low + high) / 2
    foot[2] = low[2]
    local = points - foot
    heights = local[:, 2]
    radial = np.linalg.norm(local[:, :2], axis=1)
    height = high[2] - low[2]
    radius = float(np.percentile(radial, 98))
    crown_base = max(0.05, float(heights[radial > radius * 0.36].min()))
    trunk_radius = float(np.percentile(radial[heights <= 0.12 * height], 75))
    trunk_radius = min(max(0.055, trunk_radius), 0.16 * radius)
    # The original meshes carry horizontal rings. Recover their envelope, not their painted bands.
    bins = np.rint(heights / 0.18).astype(int)
    levels, widths = [], []
    for level in np.unique(bins[heights >= crown_base]):
        samples = bins == level
        levels.append(float(np.mean(heights[samples])))
        widths.append(float(np.percentile(radial[samples], 95)))
    extent = height - crown_base
    envelope = lambda z: float(np.interp(z, levels, widths))
    rng = Random(zlib.crc32(f"{tile['id']}:{batch}".encode()))
    mesh = TreeMesh(rng)
    fork = Vector((0, 0, crown_base * 0.9))
    mesh.tube(Vector((0, 0, 0)), fork, trunk_radius, trunk_radius * 0.78)
    for i in range(7):
        phi = TAU * (i + rng.uniform(-0.2, 0.2)) / 7
        z = crown_base + extent * rng.uniform(0.18, 0.46)
        reach = envelope(z) * 0.71
        mesh.tube(
            fork,
            Vector((reach * math.cos(phi), reach * math.sin(phi), z)),
            trunk_radius * 0.43,
            trunk_radius * 0.08,
        )

    wood_vertices = len(mesh.vertices)
    green = rng.uniform(0.90, 1.10)
    rings = []
    for row in range(13):
        z = crown_base + extent * row / 12
        ring = []
        for col in range(15):
            phi = TAU * (col + 0.46 * (row % 2)) / 15
            r = (0.93 if 0 < row < 12 else 0.48) * envelope(z) * rng.uniform(0.97, 1.03)
            ring.append(
                mesh.point(
                    (r * math.cos(phi), r * math.sin(phi), z),
                    (0.083 * green, 0.145 * green, 0.056 * green),
                )
            )
        rings.append(ring)
    for a, b in pairwise(rings):
        mesh.join_rings(a, b)
    mesh.faces.extend((tuple(reversed(rings[0])), tuple(rings[-1])))

    # Stagger the shoots in azimuth and height so the measured contours do not turn into rings.
    for i in range(47):
        t = (i + rng.uniform(0.15, 0.85)) / 47
        z = crown_base + extent * t
        phi = TAU * ((i * 0.61803398875 + rng.uniform(-0.045, 0.045)) % 1)
        r = envelope(z)
        spread = min(radius * rng.uniform(0.21, 0.29), max(0.09, r * 0.56))
        middle = max(0, r - spread * 0.79)
        center = Vector((middle * math.cos(phi), middle * math.sin(phi), z))
        tone = green * rng.uniform(0.87, 1.18) * (0.94 + 0.12 * t)
        color = (0.087 * tone, 0.153 * tone, 0.058 * tone)
        mesh.cluster(center, (spread, spread, min(spread * 1.08, 0.42 * extent)), color)

    # Match the measured east/north diameters and height after the leaf silhouettes are added.
    verts = np.asarray(mesh.vertices)
    lo, hi = verts.min(axis=0), verts.max(axis=0)
    verts = (verts - lo) / np.maximum(hi - lo, 1e-6) * (high - low) + low - foot
    name = f"街路樹 {tile['id'].rsplit('-', 1)[-1]} · {batch:02}"
    data = bpy.data.meshes.new(name)
    data.from_pydata(verts.tolist(), [], mesh.faces)
    data.update()
    data.polygons.foreach_set("use_smooth", np.ones(len(data.polygons), dtype=bool))
    # Leaves scatter light around a crown. Smooth the larger envelope while retaining local leaf normals.
    raw = np.empty_like(verts, dtype=np.float32)
    data.vertices.foreach_get("normal", raw.ravel())
    shell = verts.copy()
    shell[:, 2] = (shell[:, 2] - crown_base - 0.5 * extent) * radius / extent
    shell /= np.maximum(np.linalg.norm(shell, axis=1, keepdims=True), 1e-6)
    raw[wood_vertices:] = 0.34 * raw[wood_vertices:] + 0.66 * shell[wood_vertices:]
    data.normals_split_custom_set_from_vertices(raw.tolist())
    # Per-face box projection keeps the same physical leaf scale on every side and on the top.
    loops = np.zeros(len(data.loops), dtype=np.int32)
    data.loops.foreach_get("vertex_index", loops)
    uv = np.zeros((len(loops), 2), np.float32)
    for face in data.polygons:
        corners = face.loop_indices
        normal_axis = int(np.argmax(np.abs(face.normal)))
        plane = ((1, 2), (0, 2), (0, 1))[normal_axis]
        uv[corners.start : corners.stop] = (
            verts[loops[corners.start : corners.stop]][:, plane] * 0.94
        )
    layer = data.uv_layers.new(name="Leaf scale")
    layer.uv.foreach_set("vector", uv.ravel())
    tint = data.color_attributes.new(name="Foliage", type="BYTE_COLOR", domain="POINT")
    tint.data.foreach_set("color", np.asarray(mesh.colors, dtype=np.float32).ravel())
    data.materials.append(material)
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = foot
    obj["surveyBatch"] = batch
    obj["sourceTile"] = tile["id"]
    obj["surveyHeight"] = float(height)
    obj["source"] = tile["source"]
    return obj


def foliage_texture():
    size = 256
    y, x = np.mgrid[:size, :size] / size
    rng = np.random.default_rng(2603)
    luminance = np.full((size, size), 0.70, dtype=np.float32)
    for _ in range(170):
        cx, cy, angle = rng.uniform(0, 1), rng.uniform(0, 1), rng.uniform(0, TAU)
        dx, dy = (x - cx + 0.5) % 1 - 0.5, (y - cy + 0.5) % 1 - 0.5
        length = rng.uniform(0.04, 0.085)
        u = (dx * math.cos(angle) + dy * math.sin(angle)) / length
        v = (-dx * math.sin(angle) + dy * math.cos(angle)) / (length * 0.42)
        silhouette = 1 - np.abs(u) ** 1.65 - np.abs(v) ** 1.13
        alpha = np.clip(silhouette * 9, 0, 1)
        leaf = rng.uniform(0.76, 1.29) + 0.10 * v + 0.07 * np.exp(-np.square(v * 13))
        luminance = luminance * (1 - alpha) + leaf * alpha
    rgba = np.ones((size, size, 4), dtype=np.float32)
    rgba[:, :, :3] = np.clip(luminance[:, :, None] * 0.78, 0.38, 1)
    image = bpy.data.images.new("Street foliage · leaf field", size, size)
    image.colorspace_settings.name = "Non-Color"
    image.pixels.foreach_set(rgba.ravel())
    image.pack()
    return image


def build():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    geo = runpy.run_path(str(HERE / "create-blender.py"))["geo"]
    tile_matrix, _ = geo(manifest)
    material = bpy.data.materials.new("PLATEAU · matte leaves and bark")
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Roughness"].default_value = 0.84
    color = material.node_tree.nodes.new("ShaderNodeVertexColor")
    color.layer_name = "Foliage"
    image = material.node_tree.nodes.new("ShaderNodeTexImage")
    image.image = foliage_texture()
    multiply = material.node_tree.nodes.new("ShaderNodeMixRGB")
    multiply.blend_type = "MULTIPLY"
    multiply.inputs[0].default_value = 1
    material.node_tree.links.new(image.outputs["Color"], multiply.inputs[1])
    material.node_tree.links.new(color.outputs["Color"], multiply.inputs[2])
    material.node_tree.links.new(multiply.outputs[0], bsdf.inputs["Base Color"])
    archives = {}
    groups = []
    with tempfile.TemporaryDirectory() as tmp:
        original = Path(tmp) / "tree.glb"
        for tile in (t for t in manifest["tiles"] if t["kind"] == "tree"):
            number = tile["archive"]
            if number not in archives:
                archives[number] = gzip.decompress(
                    (ASSETS / manifest["archives"][number]).read_bytes()
                )
            original.write_bytes(
                archives[number][tile["offset"] : tile["offset"] + tile["length"]]
            )
            previous = set(bpy.context.scene.objects)
            bpy.ops.import_scene.gltf(
                filepath=str(original), import_scene_as_collection=False, loglevel=4
            )
            imported = [obj for obj in bpy.context.scene.objects if obj not in previous]
            batches = defaultdict(list)
            for obj in imported:
                if obj.type != "MESH":
                    continue
                coords = np.empty((len(obj.data.vertices), 3), np.float32)
                obj.data.vertices.foreach_get("co", coords.ravel())
                matrix = np.array(tile_matrix(tile["center"]) @ obj.matrix_world)
                points = coords @ matrix[:3, :3].T + matrix[:3, 3]
                ids = np.empty(len(coords), dtype=np.int32)
                obj.data.attributes["_BATCHID"].data.foreach_get("value", ids)
                for batch in np.unique(ids):
                    batches[int(batch)].append(points[ids == batch])
            for obj in imported:
                bpy.data.objects.remove(obj, do_unlink=True)
            if len(batches) != tile["featureCount"]:
                raise ValueError(
                    f"Feature count mismatch in {tile['id']} ({len(batches)} instead of {tile['featureCount']})"
                )
            group = bpy.data.objects.new(tile["id"], None)
            bpy.context.collection.objects.link(group)
            group["sourceTile"] = tile["id"]
            for batch, chunks in sorted(batches.items()):
                model_tree(np.concatenate(chunks), tile, batch, material).parent = group
            groups.append(group)
    bpy.ops.export_scene.gltf(
        filepath=str(ASSETS / "yaesu-street-trees.glb"),
        export_format="GLB",
        export_extras=True,
        export_animations=False,
        export_vertex_color="NAME",
        export_vertex_color_name="Foliage",
        export_all_vertex_colors=False,
        export_draco_mesh_compression_enable=True,
        export_draco_mesh_compression_level=6,
    )
    print(
        f"Modelled {sum(len(g.children) for g in groups)} surveyed trees in {len(groups)} tiles"
    )


if __name__ == "__main__":
    build()
