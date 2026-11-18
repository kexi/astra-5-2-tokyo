# /// script
# requires-python = ">=3.11"
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
GRID = 300
TILE_COLLECTIONS = {
    "building": "01  PLATEAU・建築物",
    "road": "02  PLATEAU・道路",
    "water": "03  PLATEAU・水域",
    "grass": "04  PLATEAU・植生",
    "tree": "05  PLATEAU・樹木",
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


def terrain_material(name, photo, is_road=False):
    material, graph, surface = new_surface(name, (0.20, 0.22, 0.15), 0.91)
    tex = graph.nodes.new("ShaderNodeTexImage")
    tex.image = photo
    tex.label = "国土地理院・航空写真"
    tex.extension = "EXTEND"
    connect(graph, tex, "Color", surface, "Base Color")
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
    origin = Vector(
        (
            (n + alt) * cp * cl,
            (n + alt) * cp * sl,
            (n * (1 - 0.00669437999014) + alt) * sp,
        )
    )
    enu = Matrix(((-sl, cl, 0), (-sp * cl, -sp * sl, cp), (cp * cl, cp * sl, sp)))

    def tile_matrix(center):
        matrix = enu.to_4x4()
        matrix.translation = enu @ (Vector(center) - origin)
        return matrix

    def uv_project(x, y, source):
        left, top, right, bottom = source["bounds"]
        grid = 2 ** source["zoom"]
        u = ((lon + x / 90528 + 180) * grid / 360 - left) / (right - left)
        mercator = np.arcsinh(np.tan(np.radians(lat + y / 110953)))
        v = 1 - (((1 - mercator / pi) * grid / 2 - top) / (bottom - top))
        return u, v

    return tile_matrix, uv_project


def dem_sample(manifest, uv_project):
    dem = loaded_image(manifest["elevation"]["file"])
    dem.colorspace_settings.name = "Non-Color"
    pixels = np.round(array_image(dem)[..., :3] * 255).astype(np.int32)
    raw = (pixels[..., 0] << 16) | (pixels[..., 1] << 8) | pixels[..., 2]
    height = np.where(
        raw == 0x800000, 1.5, np.where(raw >= 0x800000, raw - 0x1000000, raw) * 0.01
    )
    rows, cols = height.shape

    def sample(x, y):
        u, v = uv_project(x, y, manifest["elevation"])
        xi = np.clip(np.asarray(u * cols, dtype=int), 0, cols - 1)
        yi = np.clip(np.asarray(v * rows, dtype=int), 0, rows - 1)
        return (
            height[yi, xi]
            + 36.6
            - manifest["origin"][2]
            - (x * x + y * y) / (2 * 6371000)
            - 0.78
        )

    return sample


def tree_placements(height):
    layout = json.loads((HERE / "tree-layout.json").read_text())
    return [(x, y, float(height(x, y)), scale, angle) for x, y, scale, angle in layout]


def mesh_from_ground(group, photo, manifest, project, height, waters):
    span = np.linspace(-GROUND_EXTENT, GROUND_EXTENT, GRID + 1)
    gx, gy = np.meshgrid(span, span)
    gz = height(gx, gy)
    pitch = span[1] - span[0]
    for obj in waters:
        obj.data.calc_loop_triangles()
        transformed = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
        for tri in obj.data.loop_triangles:
            a, b, c = (transformed[i] for i in tri.vertices)
            left = max(0, int((min(a.x, b.x, c.x) + GROUND_EXTENT) / pitch) - 1)
            right = min(GRID, int((max(a.x, b.x, c.x) + GROUND_EXTENT) / pitch) + 1)
            bottom = max(0, int((min(a.y, b.y, c.y) + GROUND_EXTENT) / pitch) - 1)
            top = min(GRID, int((max(a.y, b.y, c.y) + GROUND_EXTENT) / pitch) + 1)
            denom = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y)
            if abs(denom) < 0.01 or left > right or bottom > top:
                continue
            region = np.s_[bottom : top + 1, left : right + 1]
            x, y = gx[region], gy[region]
            u = ((b.y - c.y) * (x - c.x) + (c.x - b.x) * (y - c.y)) / denom
            v = ((c.y - a.y) * (x - c.x) + (a.x - c.x) * (y - c.y)) / denom
            width = pitch * 0.38 / abs(denom)
            mask = (
                (u >= -width * (b - c).length)
                & (v >= -width * (a - c).length)
                & ((1 - u - v) >= -width * (a - b).length)
            )
            gz[region] = np.where(
                mask, np.minimum(gz[region], min(a.z, b.z, c.z) - 1.2), gz[region]
            )

    vertices = np.stack((gx, gy, gz), axis=-1).reshape(-1, 3)
    step = GRID + 1
    faces = [
        (a, a + 1, a + step + 1, a + step)
        for y in range(GRID)
        for x in range(GRID)
        if (a := y * step + x) >= 0
    ]
    mesh = bpy.data.meshes.new("標高タイル・地形グリッド")
    mesh.from_pydata(vertices.tolist(), [], faces)
    mesh.update()
    obj = bpy.data.objects.new("航空写真と標高モデル", mesh)
    group.objects.link(obj)
    mesh.materials.append(terrain_material("地面・航空写真", photo))
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


def import_city(manifest, tile_matrix, photo, project, subset):
    groups = {kind: collection(name) for kind, name in TILE_COLLECTIONS.items()}
    road = terrain_material("道路・写真と舗装", photo, True)
    grass, graph, surface = new_surface("草地・緑被", (0.13, 0.19, 0.057), 0.92)
    noise_bump(graph, surface, 170, 0.51, 0.14, "草葉の起伏")
    water = water_material()
    basic_tree, graph, surface = new_surface(
        "PLATEAU樹木・樹冠", (0.08, 0.15, 0.043), 0.76
    )
    from blender_glass import enhance_building_material

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
                start = tile["offset"]
                temp.write_bytes(archive[start : start + tile["length"]])
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
                            transform.translation.z += 0.08
                        obj.matrix_world = transform
                    obj.name = f"PLATEAU・{tile['id']}"
                    obj["source"] = tile["source"]
                    for old in tuple(obj.users_collection):
                        old.objects.unlink(obj)
                    groups[kind].objects.link(obj)
                    if obj.type != "MESH":
                        continue
                    if kind in overrides:
                        slots = obj.data.materials
                        slots.clear()
                        slots.append(overrides[kind])
                        for polygon in obj.data.polygons:
                            polygon.material_index = 0
                        if kind == "road":
                            set_projected_uv(obj, manifest, project)
                        if kind == "water":
                            waters.append(obj)
                    else:
                        for material in obj.data.materials:
                            if material is not None:
                                enhance_building_material(material)
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


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--flight", type=Path, required=True)
    args.add_argument(
        "--output", type=Path, default=ROOT / "output/tokyo-station.blend"
    )
    args.add_argument("--crop", type=float)
    args.add_argument("--preview", type=Path)
    args.add_argument("--preview-frame", type=int, default=470)
    args.add_argument("--width", type=int, default=1920)
    params = args.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    bpy.ops.wm.read_factory_settings(use_empty=True)
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
    height = dem_sample(manifest, project)
    waters = import_city(manifest, placement, photo, project, params.crop)
    mesh_from_ground(
        collection("00  地理院・地形"), photo, manifest, project, height, waters
    )
    from blender_trees import create_tree_assets, place_trees

    place_trees(
        create_tree_assets(),
        tree_placements(height),
        detail_centers=((-542, -12), (-702, -15), (-560, 125)),
        near_distance=24,
        mid_distance=82,
    )
    credits = bpy.data.texts.new("出典と座標")
    credits.write(
        "東京駅上空\nProject PLATEAU 2025年度 千代田区・中央区・港区を加工\n"
        "https://docs.plateauview.mlit.go.jp/datasets/3d-tiles/\n"
        "地理院タイル（航空写真・標高）を加工して作成\n"
        "https://maps.gsi.go.jp/help/use.html\n"
        f"WGS84 原点（経度, 緯度, 楕円体高） {manifest['origin']}\n"
        "BlenderのXは東、Yは北、Zは高さ（メートル）\n"
    )
    scene.frame_set(params.preview_frame)
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.region_3d.view_perspective = "CAMERA"
                area.spaces.active.overlay.show_overlays = False
                area.spaces.active.shading.color_type = "TEXTURE"
    bpy.ops.file.pack_all()
    params.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(params.output), compress=True)
    if params.preview:
        scene.render.filepath = str(params.preview)
        bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
