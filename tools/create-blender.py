# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = []
#
# [tool.blender]
# version = "5.2"
# ///
"""Build the editable, georeferenced Tokyo Station scene using Blender's Python."""

import argparse
import gzip
import json
import sys
import tempfile
from math import asin, atan2, cos, pi, radians, sin, sqrt, tan
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ASSETS = ROOT / "assets"
sys.path.insert(0, str(HERE))
SUN = Vector((-0.84, 0.49, 0.22)).normalized()
GROUND_EXTENT = 2770
TILE_COLLECTIONS = {
    "building": "01  PLATEAU・建築物",
    "road": "02  PLATEAU・道路",
    "water": "03  PLATEAU・水域",
    "grass": "04  PLATEAU・植生",
    "tree": "05  PLATEAU・樹木",
    "bridge": "06  PLATEAU・橋梁",
    "furniture": "06  PLATEAU・都市設備",
}


def collection(name):
    group = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(group)
    return group


def connect(tree, source, output, sink, target):
    tree.links.new(source.outputs[output], sink.inputs[target])


def new_surface(name, color, roughness):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    surface = next(
        (node for node in nodes if node.bl_idname == "ShaderNodeBsdfPrincipled"), None
    )
    surface = surface or nodes.new("ShaderNodeBsdfPrincipled")
    output = next(
        (node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial"), None
    )
    output = output or nodes.new("ShaderNodeOutputMaterial")
    output.is_active_output = True
    connect(material.node_tree, surface, "BSDF", output, "Surface")
    surface.inputs["Base Color"].default_value = (*color, 1)
    surface.inputs["Roughness"].default_value = roughness
    return material, material.node_tree, surface


def noise_bump(tree, surface, scale, strength, distance, label):
    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.label = label
    noise.inputs["Scale"].default_value = scale
    noise.inputs["Detail"].default_value = 2
    noise.inputs["Roughness"].default_value = 0.67
    bump = tree.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = strength
    bump.inputs["Distance"].default_value = distance
    connect(tree, noise, "Fac", bump, "Height")
    connect(tree, bump, "Normal", surface, "Normal")
    return noise


def terrain_material(name, photo, is_road=False, overlays=()):
    material, graph, surface = new_surface(name, (0.20, 0.22, 0.15), 0.91)
    tex = graph.nodes.new("ShaderNodeTexImage")
    tex.image = photo
    tex.label = "国土地理院・航空写真"
    tex.extension = "EXTEND"
    from blender_survey import project_photos

    project_photos(graph, tex, surface, overlays, shade_banks=not is_road)
    noise_bump(
        graph,
        surface,
        210 if is_road else 425,
        0.27,
        0.035 if is_road else 0.06,
        "舗装の粒" if is_road else "土と芝の細部",
    )
    if not is_road:
        geometry = graph.nodes.new("ShaderNodeNewGeometry")
        axes = graph.nodes.new("ShaderNodeSeparateXYZ")
        connect(graph, geometry, "Position", axes, "Vector")
        extents = []
        for axis in ("X", "Y"):
            extent = graph.nodes.new("ShaderNodeMath")
            extent.operation = "ABSOLUTE"
            connect(graph, axes, axis, extent, 0)
            extents.append(extent)
        boundary = graph.nodes.new("ShaderNodeMath")
        boundary.operation = "MAXIMUM"
        connect(graph, extents[0], 0, boundary, 0)
        connect(graph, extents[1], 0, boundary, 1)
        fade = graph.nodes.new("ShaderNodeMapRange")
        fade.interpolation_type = "SMOOTHSTEP"
        fade.inputs["From Min"].default_value = 2180
        fade.inputs["From Max"].default_value = GROUND_EXTENT - 22
        connect(graph, boundary, 0, fade, "Value")
        transparency = graph.nodes.new("ShaderNodeBsdfTransparent")
        mix = graph.nodes.new("ShaderNodeMixShader")
        mix.label = "外縁を大気へフェード"
        connect(graph, fade, "Result", mix, 0)
        connect(graph, surface, "BSDF", mix, 1)
        connect(graph, transparency, "BSDF", mix, 2)
        output = next(
            node for node in graph.nodes if node.bl_idname == "ShaderNodeOutputMaterial"
        )
        connect(graph, mix, 0, output, "Surface")
    return material


def water_material():
    material, graph, surface = new_surface(
        "水面・和田倉濠", (0.045, 0.085, 0.066), 0.19
    )
    surface.inputs["IOR"].default_value = 1.333
    surface.inputs["Coat Weight"].default_value = 0.32
    surface.inputs["Coat Roughness"].default_value = 0.17
    tex = graph.nodes.new("ShaderNodeTexCoord")
    mapping = graph.nodes.new("ShaderNodeVectorMath")
    mapping.operation = "MULTIPLY"
    mapping.inputs[1].default_value = (0.48, 1.25, 1)
    connect(graph, tex, "Object", mapping, 0)
    noise = noise_bump(graph, surface, 2.5, 0.36, 0.18, "風で崩れる水紋")
    connect(graph, mapping, "Vector", noise, "Vector")
    noise.noise_dimensions = "4D"
    noise.inputs["W"].driver_add("default_value").driver.expression = "frame / 112"
    return material


def array_image(image):
    width, height = image.size
    values = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(values)
    return values.reshape((height, width, 4))


def loaded_image(name):
    return bpy.data.images.load(str(ASSETS / name), check_existing=True)


def geo(manifest):
    lon, lat, alt = manifest["origin"]
    lo, la = radians(lon), radians(lat)
    sl, cl, sp, cp = sin(lo), cos(lo), sin(la), cos(la)
    n = 6378137 / sqrt(1 - 0.00669437999014 * sp * sp)
    origin = (
        (n + alt) * cp * cl,
        (n + alt) * cp * sl,
        (n * (1 - 0.00669437999014) + alt) * sp,
    )
    enu = Matrix(((-sl, cl, 0), (-sp * cl, -sp * sl, cp), (cp * cl, cp * sl, sp)))

    def tile_matrix(center):
        matrix = enu.to_4x4()
        # Subtract in double precision before converting Earth-scale values to a Blender vector.
        matrix.translation = enu @ Vector(tuple(c - o for c, o in zip(center, origin)))
        return matrix

    def uv_project(x, y, source):
        left, top, right, bottom = source["bounds"]
        grid = 2 ** source["zoom"]
        u = ((lon + x / 90528 + 180) * grid / 360 - left) / (right - left)
        mercator = np.arcsinh(np.tan(np.radians(lat + y / 110953)))
        v = 1 - (((1 - mercator / pi) * grid / 2 - top) / (bottom - top))
        return u, v

    return tile_matrix, uv_project


def mesh_from_ground(group, photo, manifest, project, height, waters, overlays):
    from blender_survey import sample_axis

    xs = sample_axis(GROUND_EXTENT, -910, 610)
    ys = sample_axis(GROUND_EXTENT, -540, 550)
    gx, gy = np.meshgrid(xs, ys)
    gz = height(gx, gy)
    dx = np.diff(xs, prepend=xs[0])
    dy = np.diff(ys, prepend=ys[0])
    for obj in waters:
        obj.data.calc_loop_triangles()
        transformed = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
        for tri in obj.data.loop_triangles:
            a, b, c = (transformed[i] for i in tri.vertices)
            left = max(0, np.searchsorted(xs, min(a.x, b.x, c.x)) - 1)
            right = min(len(xs) - 1, np.searchsorted(xs, max(a.x, b.x, c.x)))
            bottom = max(0, np.searchsorted(ys, min(a.y, b.y, c.y)) - 1)
            top = min(len(ys) - 1, np.searchsorted(ys, max(a.y, b.y, c.y)))
            denom = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y)
            if abs(denom) < 0.01 or left > right or bottom > top:
                continue
            region = np.s_[bottom : top + 1, left : right + 1]
            x, y = gx[region], gy[region]
            u = ((b.y - c.y) * (x - c.x) + (c.x - b.x) * (y - c.y)) / denom
            v = ((c.y - a.y) * (x - c.x) + (a.x - c.x) * (y - c.y)) / denom
            width = (
                np.maximum(dx[None, left : right + 1], dy[bottom : top + 1, None])
                * 0.56
                / abs(denom)
            )
            mask = (
                (u >= -width * (b - c).length)
                & (v >= -width * (a - c).length)
                & ((1 - u - v) >= -width * (a - b).length)
            )
            gz[region] = np.where(
                mask, np.minimum(gz[region], min(a.z, b.z, c.z) - 1.2), gz[region]
            )

    vertices = np.stack((gx, gy, gz), axis=-1).reshape(-1, 3)
    step = len(xs)
    faces = [
        (a, a + 1, a + step + 1, a + step)
        for y in range(len(ys) - 1)
        for x in range(len(xs) - 1)
        if (a := y * step + x) >= 0
    ]
    mesh = bpy.data.meshes.new("標高タイル・地形グリッド")
    mesh.from_pydata(vertices.tolist(), [], faces)
    mesh.update()
    obj = bpy.data.objects.new("航空写真と標高モデル", mesh)
    group.objects.link(obj)
    mesh.materials.append(terrain_material("地面・航空写真", photo, overlays=overlays))
    set_projected_uv(obj, manifest, project)
    for face in mesh.polygons:
        face.use_smooth = True
    return obj


def set_projected_uv(obj, manifest, project):
    mesh = obj.data
    array = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", array)
    coords = array.reshape(-1, 3)
    matrix = np.array(obj.matrix_world)
    coords = coords @ matrix[:3, :3].T + matrix[:3, 3]
    u, v = project(coords[:, 0], coords[:, 1], manifest["photo"])
    uv = np.stack((u, v), axis=-1)
    ids = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", ids)
    layer = mesh.uv_layers.active or mesh.uv_layers.new(name="地理院写真・投影UV")
    layer.uv.foreach_set("vector", uv[ids].astype(np.float32).ravel())


def import_street_trees(manifest, tile_matrix, subset, group):
    selected = {
        t["id"]
        for t in manifest["tiles"]
        if t["kind"] == "tree"
        and (not subset or tile_matrix(t["center"]).translation.xy.length <= subset)
    }
    previous = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(
        filepath=str(ASSETS / "yaesu-street-trees.glb"),
        import_scene_as_collection=False,
        loglevel=4,
    )
    created = [o for o in bpy.context.scene.objects if o not in previous]
    represented = set()
    for obj in created:
        source = obj.get("sourceTile")
        if source not in selected:
            bpy.data.objects.remove(obj, do_unlink=True)
            continue
        represented.add(source)
        for old in tuple(obj.users_collection):
            old.objects.unlink(obj)
        group.objects.link(obj)
    return represented


def import_city(manifest, tile_matrix, photo, project, subset, overlays):
    groups = {kind: collection(name) for kind, name in TILE_COLLECTIONS.items()}
    street_trees = import_street_trees(manifest, tile_matrix, subset, groups["tree"])
    road = terrain_material("道路・写真と舗装", photo, True, overlays)
    grass, graph, surface = new_surface("草地・緑被", (0.13, 0.19, 0.057), 0.92)
    noise_bump(graph, surface, 170, 0.51, 0.14, "草葉の起伏")
    water = water_material()
    basic_tree, graph, surface = new_surface(
        "PLATEAU樹木・樹冠", (0.08, 0.15, 0.043), 0.76
    )
    from blender_buildings import importable_building_glb
    from blender_expressway import apply_deck_surface, deck_settings
    from blender_glass import enhance_building_material
    from blender_hedges import HEDGE_TILE, apply_hedge_surface
    from blender_street import ROAD_LIFT, STREET_TILES, apply_street_surface

    overrides = {"road": road, "grass": grass, "water": water, "tree": basic_tree}
    waters = []
    added = 0
    with tempfile.TemporaryDirectory(prefix="tokyo-blender-") as path:
        temp = Path(path) / "tile.glb"
        for index, filename in enumerate(manifest["archives"]):
            archive = gzip.decompress((ASSETS / filename).read_bytes())
            for tile in manifest["tiles"]:
                if tile["archive"] != index:
                    continue
                placement = tile_matrix(tile["center"])
                kind = tile["kind"]
                if subset and placement.translation.xy.length > subset:
                    continue
                if tile["id"] in street_trees:
                    added += 1
                    continue
                start = tile["offset"]
                glb = archive[start : start + tile["length"]]
                temp.write_bytes(
                    importable_building_glb(glb) if kind == "building" else glb
                )
                previous = set(bpy.context.scene.objects)
                bpy.ops.import_scene.gltf(
                    filepath=str(temp),
                    import_webp_texture=True,
                    import_scene_as_collection=False,
                    loglevel=4,
                )
                created = [
                    obj for obj in bpy.context.scene.objects if obj not in previous
                ]
                for obj in created:
                    if obj.parent not in created:
                        transform = placement @ obj.matrix_world
                        if kind == "water":
                            transform.translation.z += 0.70
                        elif kind == "road":
                            transform.translation.z += ROAD_LIFT
                        obj.matrix_world = transform
                    obj.name = f"PLATEAU・{tile['id']}"
                    obj["source"] = tile["source"]
                    for old in tuple(obj.users_collection):
                        old.objects.unlink(obj)
                    groups[kind].objects.link(obj)
                    if obj.type != "MESH":
                        continue
                    slots = obj.data.materials
                    has_original_photo = any(
                        material
                        and material.node_tree
                        and any(
                            node.type == "TEX_IMAGE"
                            for node in material.node_tree.nodes
                        )
                        for material in slots
                    )
                    replace_material = kind in ("water", "road") or (
                        kind in overrides and not has_original_photo
                    )
                    if replace_material:
                        slots.clear()
                        slots.append(overrides[kind])
                        for polygon in obj.data.polygons:
                            polygon.material_index = 0
                        if kind == "road":
                            set_projected_uv(obj, manifest, project)
                        if kind == "water":
                            waters.append(obj)
                    elif kind == "building":
                        for material in slots:
                            if material is not None:
                                enhance_building_material(material)
                    elif tile["id"] == HEDGE_TILE:
                        apply_hedge_surface(obj, new_surface)
                    if tile["id"] in STREET_TILES:
                        apply_street_surface(obj, tile["id"], new_surface)
                    if tile["id"] in deck_settings["tiles"]:
                        apply_deck_surface(obj, tile["id"], new_surface)
                added += 1
                if added % 100 == 0:
                    print(f"Imported {added} PLATEAU tiles", flush=True)
    print(f"Imported {added} PLATEAU tiles", flush=True)
    return waters


def setup_light(scene):
    group = collection("07  空・光源")
    world = scene.world or bpy.data.worlds.new("東京の午後・空と雲")
    world.name = "東京の午後・空と雲"
    scene.world = world
    world.use_nodes = True
    graph = world.node_tree
    background = graph.nodes.get("Background") or graph.nodes.new(
        "ShaderNodeBackground"
    )
    output = graph.nodes.get("World Output") or graph.nodes.new("ShaderNodeOutputWorld")
    connect(graph, background, "Background", output, "Surface")
    sky = graph.nodes.new("ShaderNodeTexSky")
    sky.sky_type = "MULTIPLE_SCATTERING"
    sky.sun_elevation = asin(SUN.z)
    sky.sun_rotation = atan2(SUN.y, SUN.x)
    sky.sun_size = radians(0.78)
    sky.air_density = 1.18
    sky.aerosol_density = 0.64
    sky.ozone_density = 0.73
    tex = graph.nodes.new("ShaderNodeTexCoord")
    direction = graph.nodes.new("ShaderNodeVectorMath")
    direction.operation = "SCALE"
    direction.inputs["Scale"].default_value = -1
    connect(graph, tex, "Normal", direction, 0)
    axis = graph.nodes.new("ShaderNodeSeparateXYZ")
    connect(graph, direction, "Vector", axis, "Vector")
    altitude = graph.nodes.new("ShaderNodeMapRange")
    altitude.interpolation_type = "SMOOTHSTEP"
    altitude.inputs["From Min"].default_value = 0.003
    altitude.inputs["From Max"].default_value = 0.125
    connect(graph, axis, "Z", altitude, "Value")
    horizon = graph.nodes.new("ShaderNodeMixRGB")
    horizon.label = "地平線の空気光"
    horizon.inputs[1].default_value = (2.55, 2.83, 2.90, 1)
    connect(graph, altitude, "Result", horizon, 0)
    connect(graph, sky, "Color", horizon, 2)

    minimum = graph.nodes.new("ShaderNodeMath")
    minimum.operation = "MAXIMUM"
    minimum.inputs[1].default_value = 0.09
    connect(graph, axis, "Z", minimum, 0)
    reciprocal = graph.nodes.new("ShaderNodeMath")
    reciprocal.operation = "DIVIDE"
    reciprocal.inputs[0].default_value = 1
    connect(graph, minimum, 0, reciprocal, 1)
    projection = graph.nodes.new("ShaderNodeVectorMath")
    projection.operation = "SCALE"
    connect(graph, direction, "Vector", projection, 0)
    connect(graph, reciprocal, 0, projection, "Scale")
    noise = graph.nodes.new("ShaderNodeTexNoise")
    noise.label = "上空の水平な雲層"
    noise.inputs["Scale"].default_value = 0.57
    noise.inputs["Detail"].default_value = 4
    noise.inputs["Roughness"].default_value = 0.6
    connect(graph, projection, "Vector", noise, "Vector")
    coverage = graph.nodes.new("ShaderNodeValToRGB")
    coverage.color_ramp.elements[0].position = 0.51
    coverage.color_ramp.elements[1].position = 0.68
    connect(graph, noise, "Fac", coverage, "Fac")
    elevation_mask = graph.nodes.new("ShaderNodeMapRange")
    elevation_mask.interpolation_type = "SMOOTHSTEP"
    elevation_mask.inputs["From Min"].default_value = 0.055
    elevation_mask.inputs["From Max"].default_value = 0.145
    connect(graph, axis, "Z", elevation_mask, "Value")
    clouds = graph.nodes.new("ShaderNodeMath")
    clouds.operation = "MULTIPLY"
    connect(graph, coverage, "Color", clouds, 0)
    connect(graph, elevation_mask, "Result", clouds, 1)
    mix = graph.nodes.new("ShaderNodeMixRGB")
    mix.inputs[2].default_value = (4.1, 3.89, 3.46, 1)
    connect(graph, clouds, 0, mix, 0)
    connect(graph, horizon, 0, mix, 1)
    connect(graph, mix, 0, background, "Color")
    background.inputs["Strength"].default_value = 0.145

    light = bpy.data.lights.new("太陽", "SUN")
    light.energy = 3.3
    light.angle = radians(1.55)
    light.color = (1, 0.85, 0.68)
    sun = bpy.data.objects.new("太陽・丸の内の西北西", light)
    group.objects.link(sun)
    sun.rotation_euler = (-SUN).to_track_quat("-Z", "Y").to_euler()

    bounce = bpy.data.lights.new("街並みの反射光", "SUN")
    bounce.energy = 0.35
    bounce.color = (1.0, 0.87, 0.74)
    bounce.use_shadow = False
    bounce_object = bpy.data.objects.new("天空光・暖色の補助", bounce)
    group.objects.link(bounce_object)
    bounce_object.rotation_euler = (
        Vector((0.4, 0.25, -1)).to_track_quat("-Z", "Y").to_euler()
    )


def setup_compositor(scene):
    layer = scene.view_layers[0]
    layer.use_pass_mist = True
    layer.use_pass_z = True
    scene.world.mist_settings.start = 640
    scene.world.mist_settings.depth = 2600
    scene.world.mist_settings.falloff = "QUADRATIC"
    graph = bpy.data.node_groups.new("深度フォグ・太陽のグロー", "CompositorNodeTree")
    scene.compositing_node_group = graph
    graph.interface.new_socket(
        name="Image", in_out="OUTPUT", socket_type="NodeSocketColor"
    )
    render = graph.nodes.new("CompositorNodeRLayers")
    render.location = (-460, 0)
    mist = graph.nodes.new("ShaderNodeMath")
    mist.operation = "MULTIPLY"
    mist.location = (-140, -145)
    geometry = graph.nodes.new("ShaderNodeMath")
    geometry.operation = "LESS_THAN"
    geometry.inputs[1].default_value = 14500
    geometry.location = (-340, -175)
    connect(graph, render, "Depth", geometry, 0)
    connect(graph, render, "Mist", mist, 0)
    connect(graph, geometry, 0, mist, 1)
    mix = graph.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs[7].default_value = (0.37, 0.41, 0.42, 1)
    mix.location = (-20, 0)
    connect(graph, mist, 0, mix, 0)
    connect(graph, render, "Image", mix, 6)
    glare = graph.nodes.new("CompositorNodeGlare")
    glare.location = (220, 0)
    glare.inputs["Type"].default_value = "Fog Glow"
    glare.inputs["Threshold"].default_value = 2.1
    connect(graph, mix, 2, glare, "Image")
    ghosts = graph.nodes.new("CompositorNodeGlare")
    ghosts.location = (440, 0)
    ghosts.inputs["Type"].default_value = "Ghosts"
    ghosts.inputs["Threshold"].default_value = 4.5
    ghosts.inputs["Strength"].default_value = 0.075
    connect(graph, glare, "Image", ghosts, "Image")
    output = graph.nodes.new("NodeGroupOutput")
    output.location = (690, 0)
    connect(graph, ghosts, "Image", output, "Image")


def browser_to_blender(values):
    x, height, z = values
    return Vector((x, -z, height))


def position_camera(camera, frame):
    camera.location = browser_to_blender(frame["position"])
    direction = browser_to_blender(frame["target"]) - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    camera.data.lens = camera.data.sensor_height / (2 * tan(radians(frame["fov"]) / 2))


def setup_cameras(scene, path):
    group = collection("08  ドローン・カメラ")
    camera_data = bpy.data.cameras.new("ドローン・レンズ")
    camera_data.clip_start = 0.15
    camera_data.clip_end = 16000
    camera_data.sensor_fit = "VERTICAL"
    camera = bpy.data.objects.new("ドローン・編集済み飛行経路", camera_data)
    group.objects.link(camera)
    scene.camera = camera
    flight = json.loads(path.read_text())
    scene.render.fps = flight["fps"]
    scene.frame_start = 0
    scene.frame_end = len(flight["frames"]) - 1
    last = None
    for number, frame in enumerate(flight["frames"]):
        position_camera(camera, frame)
        camera.keyframe_insert(data_path="location", frame=number)
        camera.keyframe_insert(data_path="rotation_euler", frame=number)
        camera.data.keyframe_insert(data_path="lens", frame=number)
        if last != frame["shot"]:
            scene.timeline_markers.new(frame["name"], frame=number)
            last = frame["shot"]
    scene.frame_set(min(350, scene.frame_end))
    return camera


def main(argv=None, *, empty_scene=False):
    args = argparse.ArgumentParser()
    args.add_argument("--flight", type=Path, required=True)
    args.add_argument(
        "--output", type=Path, default=ROOT / "output/tokyo-station.blend"
    )
    args.add_argument("--crop", type=float)
    args.add_argument("--preview", type=Path)
    args.add_argument("--preview-frame", type=int, default=470)
    args.add_argument("--width", type=int, default=1920)
    args.add_argument(
        "--export-buildings", type=Path, help="Write the 22-building web GLB"
    )
    params = args.parse_args(
        argv if argv is not None else sys.argv[sys.argv.index("--") + 1 :]
    )
    if not empty_scene:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    elif bpy.context.scene.objects or bpy.data.filepath:
        raise RuntimeError("The MCP build requires its own empty Blender instance")
    scene = bpy.context.scene
    scene.name = "東京駅上空・PLATEAU 2025"
    scene.unit_settings.system = "METRIC"
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = 16
    scene.eevee.use_raytracing = True
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.04
    scene.render.resolution_x = params.width
    scene.render.resolution_y = round(params.width * 9 / 16 / 2) * 2
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    setup_light(scene)
    setup_compositor(scene)
    setup_cameras(scene, params.flight)
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    placement, project = geo(manifest)
    photo = loaded_image(manifest["photo"]["file"])
    from blender_survey import (
        dem_sample,
        import_rail_model,
        reference_curves,
        tree_placements,
        world_photos,
    )
    from blender_trees import create_tree_assets, place_trees

    survey = json.loads((ASSETS / "survey.json").read_text())
    park_ground = json.loads((ASSETS / "park-ground.json").read_text())
    railway = json.loads((ASSETS / "rail-reference.json").read_text())
    geoid = railway["verticalDatum"]["geoidHeight"]
    height = dem_sample(
        manifest, project, survey["elevations"], loaded_image, array_image, geoid
    )
    overlays = world_photos(
        [park_ground["layer"], *survey["photos"]], project, loaded_image
    )
    waters = import_city(manifest, placement, photo, project, params.crop, overlays)
    mesh_from_ground(
        collection("00  地理院と東京都・地形"),
        photo,
        manifest,
        project,
        height,
        waters,
        overlays,
    )
    heights = json.loads((ASSETS / survey["trees"]["file"]).read_text())["heights"]
    layout = json.loads((HERE / "tree-layout.json").read_text())
    layout.extend(tree[:4] for tree in park_ground["trees"])
    heights.extend(tree[4] for tree in park_ground["trees"])
    positions = tree_placements(
        layout, height, heights, manifest.get("surveyedTrees", [])
    )
    place_trees(
        create_tree_assets(),
        positions,
        detail_centers=((-542, -12), (-702, -15), (-560, 125)),
        near_distance=24,
        mid_distance=82,
    )
    reference_curves(scene, railway, height)
    import_rail_model(scene, ASSETS / "rail-model.glb")
    from blender_omy import (
        export_buildings,
        extract_buildings,
        import_model,
        save_study,
    )

    landmarks = collection("10  大丸有・建物別の外装写真")
    extract_buildings(scene, landmarks)
    if params.export_buildings:
        export_buildings(landmarks, params.export_buildings)
    rooftop = collection("10  丸の内二丁目ビル・測量による外装")
    basement = collection("11  東京駅周辺地下街・LOD4")
    import_model(scene, rooftop, ASSETS / "marunouchi-nichome.glb")
    import_model(scene, basement, ASSETS / "omy-underground.glb")
    credits = bpy.data.texts.new("出典と座標")
    credits.write(
        "東京駅上空\nProject PLATEAU 2025年度 千代田区・中央区・港区を加工\n"
        "https://docs.plateauview.mlit.go.jp/datasets/3d-tiles/\n"
        "地理院タイル（航空写真・標高）を加工して作成\n"
        "https://maps.gsi.go.jp/help/use.html\n"
        "東京都デジタルツイン実現プロジェクト・区部点群データ（CC BY 4.0）を加工\n"
        "https://www.metro.tokyo.lg.jp/information/press/2024/10/2024103126\n"
        "国土交通省・国土数値情報 鉄道（N02、2025年度、CC BY 4.0）を加工\n"
        "https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N02-2025.html\n"
        "© OpenStreetMap contributors・軌道中心線を加工（ODbL）\n"
        "https://www.openstreetmap.org/copyright\n"
        f"WGS84 原点（経度, 緯度, 楕円体高） {manifest['origin']}\n"
        "BlenderのXは東、Yは北、Zは高さ（メートル）\n"
    )
    scene.frame_set(params.preview_frame)
    for source in (
        "OMY-SOURCES.md",
        "OMY-FACADE-REPAIRS.md",
        "MARUNOUCHI-SOURCES.md",
        "OMY-UNDERGROUND-SOURCES.md",
    ):
        credits.write("\n" + (ASSETS / source).read_text() + "\n")
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.region_3d.view_perspective = "CAMERA"
                area.spaces.active.overlay.show_overlays = False
                area.spaces.active.shading.color_type = "TEXTURE"
    bpy.ops.file.pack_all()
    params.output.parent.mkdir(parents=True, exist_ok=True)
    if not params.crop:
        save_study(
            scene,
            (landmarks, rooftop, basement),
            credits,
            params.output.with_name("omy-architecture.blend"),
        )
    from blender_buildings import separate_buildings

    separate_buildings(
        scene.collection.children[TILE_COLLECTIONS["building"]], landmarks
    )
    bpy.ops.wm.save_as_mainfile(filepath=str(params.output), compress=True)
    if params.preview:
        scene.render.filepath = str(params.preview)
        bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
