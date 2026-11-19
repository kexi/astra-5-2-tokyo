# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = []
#
# [tool.blender]
# version = "5.2"
# ///
"""広葉樹の生成と配置。Blender の Z-up 座標、実寸メートルで扱う。"""

from math import cos, pi, sin, tau
from random import Random

import bpy
import numpy as np
from mathutils import Vector

SPECIES = ("Zelkova", "Camphor")
LEVELS = ("near", "mid", "far")
UP = Vector((0, 0, 1))


def _material(name, color):
    material = bpy.data.materials.new(name)
    material.diffuse_color = (*color, 1)
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = 0.76
    return material, bsdf


def _bark_material():
    material, bsdf = _material("Park | fissured bark", (0.13, 0.091, 0.056))
    nodes, link = material.node_tree.nodes, material.node_tree.links.new
    coords = nodes.new("ShaderNodeTexCoord")
    scale = nodes.new("ShaderNodeVectorMath")
    scale.operation = "MULTIPLY"
    scale.inputs[1].default_value = (15, 15, 2.3)
    link(coords.outputs["Object"], scale.inputs[0])
    fissures = nodes.new("ShaderNodeTexVoronoi")
    fissures.feature = "DISTANCE_TO_EDGE"
    fissures.inputs["Scale"].default_value = 1.9
    link(scale.outputs[0], fissures.inputs["Vector"])
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.008
    ramp.color_ramp.elements[0].color = (0.037, 0.029, 0.023, 1)
    ramp.color_ramp.elements[1].position = 0.23
    ramp.color_ramp.elements[1].color = (0.225, 0.164, 0.114, 1)
    link(fissures.outputs["Distance"], ramp.inputs[0])
    link(ramp.outputs[0], bsdf.inputs["Base Color"])
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.29
    bump.inputs["Distance"].default_value = 0.012
    link(fissures.outputs["Distance"], bump.inputs["Height"])
    link(bump.outputs[0], bsdf.inputs["Normal"])
    return material


def _leaf_map():
    size = 384
    u, v = np.meshgrid(
        (np.arange(size) + 0.5) / size * 2 - 1, (np.arange(size) + 0.5) / size
    )
    midrib = np.exp(-((u / (0.025 + 0.016 * (1 - v))) ** 2))
    ribs = 7.5 * v - (1.02 * np.abs(u) - 0.30 * u * u)
    veins = (
        np.exp(-((np.sin(pi * ribs) / 0.12) ** 2)) * (1 - np.abs(u) ** 2) * (1 - midrib)
    )
    mottling = 0.038 * np.sin(19 * v + 4 * np.sin(5 * u)) + 0.027 * np.cos(
        12 * u + 18 * v
    )
    rgbs = np.stack(
        [
            0.175 + mottling + 0.19 * midrib + 0.065 * veins,
            0.289 + mottling + 0.15 * midrib + 0.064 * veins,
            0.068 + mottling * 0.5 + 0.068 * midrib + 0.018 * veins,
            np.ones_like(u),
        ],
        axis=-1,
    )
    image = bpy.data.images.new(
        "Broadleaf | midrib and secondary veins", width=size, height=size
    )
    image.colorspace_settings.name = "Non-Color"
    image.pixels.foreach_set(rgbs.astype(np.float32).ravel())
    image.pack()
    return image


def _leaf_material(image):
    material, bsdf = _material("Park | leaf lamina and veins", (0.17, 0.29, 0.068))
    nodes, link = material.node_tree.nodes, material.node_tree.links.new
    image_node = nodes.new("ShaderNodeTexImage")
    image_node.image = image
    tint = nodes.new("ShaderNodeVertexColor")
    tint.layer_name = "Tint"
    mix = nodes.new("ShaderNodeMixRGB")
    mix.blend_type = "MULTIPLY"
    mix.inputs[0].default_value = 1
    link(image_node.outputs["Color"], mix.inputs[1])
    link(tint.outputs["Color"], mix.inputs[2])
    link(mix.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.52
    bsdf.inputs["Subsurface Weight"].default_value = 0.085
    bsdf.inputs["Subsurface Radius"].default_value = (0.18, 0.29, 0.055)
    bsdf.inputs["Subsurface Scale"].default_value = 0.006
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.13
    bump.inputs["Distance"].default_value = 0.001
    link(image_node.outputs["Color"], bump.inputs["Height"])
    link(bump.outputs[0], bsdf.inputs["Normal"])
    translucent = nodes.new("ShaderNodeBsdfTranslucent")
    link(mix.outputs["Color"], translucent.inputs["Color"])
    mix_shader = nodes.new("ShaderNodeMixShader")
    mix_shader.inputs[0].default_value = 0.28
    link(bsdf.outputs[0], mix_shader.inputs[1])
    link(translucent.outputs[0], mix_shader.inputs[2])
    link(mix_shader.outputs[0], nodes.get("Material Output").inputs["Surface"])
    return material


class Geometry:
    def __init__(self):
        self.positions = []
        self.faces = []
        self.uvs = []
        self.colors = []

    def vertex(self, pos, uv=None, color=None):
        index = len(self.positions)
        self.positions.append(tuple(pos))
        if uv is not None:
            self.uvs.append(uv)
        if color is not None:
            self.colors.append(color)
        return index

    def mesh(self, name, material, collection):
        data = bpy.data.meshes.new(name)
        data.from_pydata(self.positions, [], self.faces)
        data.update()
        data.materials.append(material)
        if self.uvs:
            layer = data.uv_layers.new(name="Blade coordinates")
            indices = np.empty(len(data.loops), dtype=np.int32)
            data.loops.foreach_get("vertex_index", indices)
            layer.data.foreach_set(
                "uv", np.asarray(self.uvs, dtype=np.float32)[indices].ravel()
            )
        if self.colors:
            colors = data.color_attributes.new(
                name="Tint", type="BYTE_COLOR", domain="POINT"
            )
            colors.data.foreach_set(
                "color", np.asarray(self.colors, dtype=np.float32).ravel()
            )
        if data.polygons:
            data.polygons.foreach_set(
                "use_smooth", np.ones(len(data.polygons), dtype=bool)
            )
        obj = bpy.data.objects.new(name, data)
        collection.objects.link(obj)
        return obj


def _tube(mesh, points, radii, sides):
    last = None
    for i, (center, radius) in enumerate(zip(points, radii)):
        axis = (
            points[min(i + 1, len(points) - 1)] - points[max(0, i - 1)]
        ).normalized()
        normal = axis.cross(Vector((0, 1, 0))).normalized()
        tangent = axis.cross(normal)
        ring = []
        for j in range(sides):
            angle = tau * j / sides
            rib = radius * (1 + 0.085 * cos(3 * angle + 0.57) + 0.045 * sin(5 * angle))
            ring.append(
                mesh.vertex(center + rib * (normal * cos(angle) + tangent * sin(angle)))
            )
        if last:
            for j in range(sides):
                k = (j + 1) % sides
                mesh.faces.append((last[j], last[k], ring[k], ring[j]))
        last = ring


def _blade(mesh, stem, heading, length, width, rng, species, level):
    azimuth = heading + rng.uniform(-0.26, 0.26)
    horizontal = Vector((cos(azimuth), sin(azimuth), 0))
    pitch = rng.uniform(-0.56, 0.49)
    axis = (horizontal + UP * pitch).normalized()
    lateral = Vector(
        (-horizontal.y, horizontal.x, rng.uniform(-0.52, 0.52))
    ).normalized()
    up = axis.cross(lateral).normalized()
    twist = rng.uniform(-0.65, 0.65)
    fold = rng.uniform(0.17, 0.36)
    shade = rng.uniform(0.45, 1)
    color = (
        0.66 * shade + rng.uniform(0.02, 0.20),
        0.86 * shade + 0.11,
        0.54 * shade + 0.10,
        1,
    )
    base = mesh.vertex(stem, (0.5, 0), color)
    last = None
    fractions = (
        (0.16, 0.33, 0.51, 0.69, 0.85) if level == "near" else (0.27, 0.56, 0.79)
    )
    for i, t in enumerate(fractions):
        width_t = width * sin(pi * t) ** (0.73 if species == "Camphor" else 0.94)
        belly = width_t * (1 + (0.105 if i % 2 == 0 and species == "Zelkova" else 0))
        center = (
            stem
            + axis * (length * t)
            + up * (length * (0.15 * sin(pi * t) - 0.105 * t * t))
        )
        side = (lateral + up * (twist * (t - 0.33))).normalized()
        ridge = mesh.vertex(center + up * belly * fold, (0.5, t), color)
        left = mesh.vertex(center + side * belly, (0, t), color)
        right = mesh.vertex(center - side * belly * 0.96, (1, t), color)
        if last:
            l, m, r = last
            mesh.faces.extend(((l, left, ridge, m), (m, ridge, right, r)))
        else:
            mesh.faces.extend(((base, left, ridge), (base, ridge, right)))
        last = (left, ridge, right)
    tip = mesh.vertex(stem + axis * length - up * (0.105 * length), (0.5, 1), color)
    l, m, r = last
    mesh.faces.extend(((l, tip, m), (m, tip, r)))


def _clump(mesh, center, radius, rng, shade, rows=4, sides=8):
    bottom = mesh.vertex(center - UP * radius.z)
    last = []
    for i in range(1, rows):
        phi = pi * i / rows
        ring = []
        for j in range(sides):
            theta = tau * (j + 0.17 * i) / sides
            r = rng.uniform(0.82, 1.12)
            p = center + Vector(
                (
                    radius.x * sin(phi) * cos(theta) * r,
                    radius.y * sin(phi) * sin(theta) * r,
                    -radius.z * cos(phi) * r,
                )
            )
            ring.append(mesh.vertex(p))
        for j in range(sides):
            k = (j + 1) % sides
            mesh.faces.append(
                (bottom, ring[k], ring[j])
                if not last
                else (last[j], last[k], ring[k], ring[j])
            )
        last = ring
    top = mesh.vertex(center + UP * radius.z)
    for j in range(sides):
        mesh.faces.append((last[j], last[(j + 1) % sides], top))
    mesh.colors.extend(
        [(shade * 0.72, shade, shade * 0.61, 1)] * (rows * sides - sides + 2)
    )


def _wind(obj):
    positions = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", positions)
    base = positions.reshape(-1, 3)
    x, y, z = base.T
    height = np.clip(z / 9, 0, 1.3)
    weight = height**1.75
    uv = np.sin(1.4 * x + 0.65 * y + 0.28 * z)
    obj.shape_key_add(name="Rest")
    for name, phase, period in (("Branch sway", 0.3, 131), ("Leaf flutter", 2.1, 65)):
        target = base.copy()
        sway = (0.11 if name == "Branch sway" else 0.034) * weight
        target[:, 0] += sway * (0.78 + np.sin(0.86 * x + 0.92 * z + phase))
        target[:, 1] += sway * (0.32 + np.cos(0.63 * y + 0.75 * z + phase))
        target[:, 2] += sway * 0.35 * uv
        key = obj.shape_key_add(name=name)
        key.data.foreach_set("co", target.astype(np.float32).ravel())
        for frame, value in (
            (1, 0.5),
            (1 + period * 0.25, 1),
            (1 + period * 0.5, 0.5),
            (1 + period * 0.75, 0),
            (1 + period, 0.5),
        ):
            key.value = value
            key.keyframe_insert(data_path="value", frame=frame)
    keys = obj.data.shape_keys
    action = keys.animation_data.action
    for layer in action.layers:
        for strip in layer.strips:
            for channelbag in strip.channelbags:
                for curve in channelbag.fcurves:
                    curve.modifiers.new("CYCLES")


def _specimen(species, level, bark_material, leaf_material, distant_material):
    collection = bpy.data.collections.new(f"ParkTree.{species}.{level}")
    collection.use_fake_user = True
    bark, canopy = Geometry(), Geometry()
    rng = Random(19673 if species == "Zelkova" else 4038)
    broad = species == "Camphor"
    trunk = [
        Vector(p)
        for p in (
            (0, 0, -0.24),
            (0.025, 0.045, 0.37),
            (-0.06, 0.08, 1.65),
            (0.14, 0.03, 3.35),
            (0.18, -0.16, 5.0),
            (0.02, 0.035, 6.5),
        )
    ]
    _tube(
        bark,
        trunk,
        (0.49, 0.36, 0.28, 0.215, 0.139, 0.065),
        12 if level == "near" else 7,
    )
    for j in range(6):
        angle = tau * j / 6 + rng.uniform(-0.25, 0.25)
        radial = Vector((cos(angle), sin(angle), 0))
        _tube(
            bark,
            (trunk[1], radial * 0.49 + UP * 0.095, radial * 0.95 - UP * 0.06),
            (0.18, 0.139, 0.035),
            6,
        )
    leaders = 12
    for i in range(leaders):
        tier = (i + 0.5) / leaders
        angle = 2.399963 * i + rng.uniform(-0.17, 0.17)
        radial = Vector((cos(angle), sin(angle), 0))
        side = Vector((-radial.y, radial.x, 0))
        reach = (3.29 if broad else 2.65) * (1 - 0.52 * tier) * rng.uniform(0.86, 1.14)
        base = Vector((0.12, 0.02, 1.80 + 4.4 * tier))
        elbow = base + radial * reach * 0.44 + UP * (0.91 + 0.17 * tier)
        tip = base + radial * reach + UP * ((1.72 if broad else 2.13) + 0.33 * tier)
        if level == "horizon":
            _clump(
                canopy,
                tip + UP * 0.52,
                Vector((1.46, 1.49, 1.13)),
                rng,
                rng.uniform(0.61, 0.95),
                rows=3,
                sides=5,
            )
            continue
        _tube(
            bark,
            (base, elbow, tip),
            (0.136 - 0.07 * tier, 0.092 - 0.044 * tier, 0.027),
            7 if level == "near" else 5,
        )
        for j in range(5):
            t = 0.20 + 0.17 * j
            seat = base.lerp(elbow, t * 2) if t < 0.5 else elbow.lerp(tip, t * 2 - 1)
            sign = -1 if j % 2 else 1
            axis = (
                radial * 0.59
                + side * (sign * (0.94 - 0.23 * t))
                + UP * (0.63 + 0.34 * t)
            ).normalized()
            end = seat + axis * (1.46 - 0.25 * t)
            _tube(
                bark,
                (seat, seat.lerp(end, 0.54) - UP * 0.08, end),
                (0.058, 0.037, 0.011),
                5,
            )
            if level == "far":
                _clump(
                    canopy,
                    end + UP * 0.19,
                    Vector((1.03, 1.03, 0.85)),
                    rng,
                    rng.uniform(0.55, 1),
                )
                continue
            for k in range(6):
                at = 0.24 + 0.148 * k
                fork = seat.lerp(end, at)
                az = (
                    angle
                    + sign * (0.35 + 0.35 * t)
                    + (1 if k % 2 else -1) * (0.66 + 0.13 * t)
                )
                twig_axis = Vector(
                    (cos(az), sin(az), 0.48 + 0.16 * (k % 3))
                ).normalized()
                fork_end = fork + twig_axis * (0.94 - 0.20 * at)
                _tube(bark, (fork, fork_end), (0.023, 0.005), 4)
                for q in range(4 if level == "near" else 2):
                    leaf_rng = Random(
                        163 * i + 853 * j + 5917 * k + 977 * q + (133 if broad else 801)
                    )
                    f = (0.25 + 0.23 * q) if level == "near" else (0.40 + 0.42 * q)
                    bud = fork.lerp(fork_end, f)
                    shoot = az + (1 if q % 2 else -1) * leaf_rng.uniform(0.72, 1.19)
                    shoot_axis = Vector(
                        (cos(shoot), sin(shoot), leaf_rng.uniform(-0.39, 0.49))
                    ).normalized()
                    shoot_end = bud + shoot_axis * leaf_rng.uniform(0.46, 0.72)
                    if level == "near":
                        _tube(bark, (bud, shoot_end), (0.009, 0.0025), 3)
                    for leaf in range(8 if level == "near" else 5):
                        s = (leaf + 0.28) / (8 if level == "near" else 5)
                        stem = bud.lerp(shoot_end, s)
                        direction = shoot + (1 if leaf % 2 else -1) * leaf_rng.uniform(
                            0.57, 1.30
                        )
                        length = leaf_rng.uniform(0.115, 0.205) * (
                            1 if level == "near" else 1.85
                        )
                        if broad:
                            length *= 1.12
                        _blade(
                            canopy,
                            stem,
                            direction,
                            length,
                            length * leaf_rng.uniform(0.19, 0.27),
                            leaf_rng,
                            species,
                            level,
                        )
    wood = bark.mesh(f"{species}.{level}.Bark", bark_material, collection)
    leaves = canopy.mesh(
        f"{species}.{level}.Foliage",
        distant_material if level in ("far", "horizon") else leaf_material,
        collection,
    )
    if level in ("near", "mid"):
        _wind(wood)
        _wind(leaves)
    return collection


def create_tree_assets():
    """Return species -> level -> unlinked collection, ready for native instances."""
    bark = _bark_material()
    leaf = _leaf_material(_leaf_map())
    distant, bsdf = _material("Park | distant crown", (0.078, 0.143, 0.045))
    nodes, link = distant.node_tree.nodes, distant.node_tree.links.new
    tint = nodes.new("ShaderNodeVertexColor")
    tint.layer_name = "Tint"
    mix = nodes.new("ShaderNodeMixRGB")
    mix.blend_type = "MULTIPLY"
    mix.inputs[0].default_value = 1
    mix.inputs[1].default_value = (0.095, 0.17, 0.053, 1)
    link(tint.outputs["Color"], mix.inputs[2])
    grain = nodes.new("ShaderNodeTexNoise")
    grain.inputs["Scale"].default_value = 52
    grain.inputs["Detail"].default_value = 2.9
    grain.inputs["Roughness"].default_value = 0.64
    foliage_color = nodes.new("ShaderNodeMixRGB")
    foliage_color.blend_type = "MULTIPLY"
    foliage_color.inputs[0].default_value = 0.71
    link(mix.outputs[0], foliage_color.inputs[1])
    link(grain.outputs["Fac"], foliage_color.inputs[2])
    link(foliage_color.outputs[0], bsdf.inputs["Base Color"])
    facing = nodes.new("ShaderNodeLayerWeight")
    facing.inputs["Blend"].default_value = 0.42
    threshold = nodes.new("ShaderNodeMapRange")
    threshold.inputs["To Min"].default_value = 0.25
    threshold.inputs["To Max"].default_value = 0.52
    link(facing.outputs["Facing"], threshold.inputs["Value"])
    leaf_gaps = nodes.new("ShaderNodeMath")
    leaf_gaps.operation = "GREATER_THAN"
    link(grain.outputs["Fac"], leaf_gaps.inputs[0])
    link(threshold.outputs["Result"], leaf_gaps.inputs[1])
    link(leaf_gaps.outputs[0], bsdf.inputs["Alpha"])
    return {
        species: {
            level: _specimen(species, level, bark, leaf, distant)
            for level in (*LEVELS, "horizon")
        }
        for species in SPECIES
    }


def place_trees(
    assets,
    placements,
    *,
    detail_centers=(),
    near_distance=45,
    mid_distance=155,
    parent_collection=None,
):
    """Place linked collections at (x,y,z[,scale,rotation]); distances are horizontal metres."""
    container = bpy.data.collections.new("Park | broadleaved trees")
    (parent_collection or bpy.context.scene.collection).children.link(container)
    centers = list(detail_centers)
    for i, row in enumerate(placements):
        x, y, z = row[:3]
        source_index = row[6] if len(row) > 6 else i
        rng = Random(35927 + source_index)
        scale = row[3] if len(row) > 3 else rng.uniform(0.79, 1.18)
        angle = row[4] if len(row) > 4 else rng.uniform(0, tau)
        distance2 = min(
            ((x - c[0]) ** 2 + (y - c[1]) ** 2 for c in centers), default=float("inf")
        )
        level = (
            "near"
            if distance2 <= near_distance**2
            else "mid"
            if distance2 <= mid_distance**2
            else "far"
        )
        species = SPECIES[source_index % 2]
        tree = bpy.data.objects.new(f"{species} | {i:04}", None)
        tree.instance_type = "COLLECTION"
        tree.instance_collection = assets[species][level]
        tree.empty_display_size = 0.18
        tree.location = (x, y, z)
        tree.rotation_euler.z = angle
        height = row[5] if len(row) > 5 else None
        original_height = 9.4745 if species == "Zelkova" else 9.204
        vertical = scale if height is None else height / original_height
        tree.scale = (scale, scale, vertical)
        container.objects.link(tree)
    return container


def export_web_assets(assets, output_directory):
    """Export the same meshes for the live viewer; its vertex shader supplies wind."""
    from pathlib import Path

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    scene_root = bpy.context.scene.collection
    for level in (*LEVELS, "horizon"):
        collections = [assets[species][level] for species in SPECIES]
        for collection in collections:
            scene_root.children.link(collection)
        bpy.ops.object.select_all(action="DESELECT")
        for collection in collections:
            for obj in collection.objects:
                obj.select_set(True)
        bpy.ops.export_scene.gltf(
            filepath=str(output / f"park-trees-{level}.glb"),
            use_selection=True,
            export_format="GLB",
            export_animations=False,
            export_morph=False,
            export_materials="NONE",
            export_vertex_color="ACTIVE",
            export_draco_mesh_compression_enable=True,
            export_draco_mesh_compression_level=7,
            export_draco_position_quantization=14,
            export_draco_normal_quantization=9,
            export_draco_texcoord_quantization=11,
            export_draco_color_quantization=7,
        )
        for collection in collections:
            scene_root.children.unlink(collection)


def bake_foliage_atlases(assets, output_directory):
    """Bake actual leaf silhouettes across azimuth and elevation for distant instances."""
    from pathlib import Path
    from tempfile import TemporaryDirectory

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    scene = bpy.data.scenes.new("Park | canopy atlas studio")
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 8
    scene.cycles.use_denoising = True
    scene.render.film_transparent = True
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    scene.world = bpy.data.worlds.new("Park | atlas skylight")
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.78, 0.86, 1, 1)
    background.inputs["Strength"].default_value = 0.86
    sun_data = bpy.data.lights.new("Park | atlas daylight", type="SUN")
    sun_data.energy = 2.6
    sun_data.color = (1, 0.91, 0.74)
    sun_data.angle = 0.09
    light = bpy.data.objects.new(sun_data.name, sun_data)
    scene.collection.objects.link(light)
    camera_data = bpy.data.cameras.new("Park | orthographic canopy")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 12
    camera = bpy.data.objects.new(camera_data.name, camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    elevations = (6, 40, 74)
    count = 4
    size = 288
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    specimen = bpy.data.objects.new("Park | atlas subject", None)
    specimen.instance_type = "COLLECTION"
    scene.collection.objects.link(specimen)
    center = Vector((0, 0, 4.65))
    with TemporaryDirectory() as temporary:
        scene.render.filepath = str(Path(temporary) / "view.png")
        for species in SPECIES:
            specimen.instance_collection = assets[species]["near"]
            pixels = np.zeros(
                (len(elevations) * size, count * size, 4), dtype=np.float32
            )
            for row, elevation in enumerate(elevations):
                e = elevation * pi / 180
                for col in range(count):
                    a = tau * col / count
                    ray = Vector((sin(a) * cos(e), -cos(a) * cos(e), sin(e)))
                    camera.location = center + ray * 24
                    camera.rotation_euler = (-ray).to_track_quat("-Z", "Y").to_euler()
                    # Keep cross-light consistent across the four atlas orientations.
                    light_dir = Vector((sin(a - 0.88), -cos(a - 0.88), 0.68))
                    light.rotation_euler = (
                        (-light_dir).to_track_quat("-Z", "Y").to_euler()
                    )
                    bpy.ops.render.render(write_still=True, scene=scene.name)
                    view = bpy.data.images.load(
                        scene.render.filepath, check_existing=False
                    )
                    data = np.empty(size * size * 4, dtype=np.float32)
                    view.pixels.foreach_get(data)
                    pixels[
                        row * size : (row + 1) * size, col * size : (col + 1) * size
                    ] = data.reshape(size, size, 4)
                    bpy.data.images.remove(view)
            atlas = bpy.data.images.new(
                f"Park | {species} canopy",
                width=count * size,
                height=len(elevations) * size,
                alpha=True,
            )
            atlas.pixels.foreach_set(pixels.ravel())
            atlas.file_format = "PNG"
            atlas.filepath_raw = str(output / f"park-foliage-{species.lower()}.png")
            atlas.save()
            bpy.data.images.remove(atlas)
    bpy.data.scenes.remove(scene)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    destination = (
        Path(args[0]) if args else Path(__file__).resolve().parents[1] / "assets"
    )
    assets = create_tree_assets()
    export_web_assets(assets, destination)
    bake_foliage_atlases(assets, destination)
