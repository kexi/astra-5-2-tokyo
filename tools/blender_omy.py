"""Prepare editable principal building meshes and their individual paint atlases."""

import json
import struct
import tempfile
import zlib
from collections import defaultdict
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
CASE_STUDY = "https://bim-design.com/uploads/Mitsubishi-Jisho-CaseStudy_4P_ja.pdf"


def write_png(path, rgb):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    height, width = rgb.shape[:2]
    header = struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + line.tobytes() for line in rgb[::-1])
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 6))
        + chunk(b"IEND", b"")
    )


def cover_triangle(mask, points):
    height, width = mask.shape
    x0, y0 = np.maximum(np.floor(points.min(axis=0)).astype(int) - 1, 0)
    x1, y1 = np.minimum(np.ceil(points.max(axis=0)).astype(int) + 1, (width, height))
    y, x = np.ogrid[y0:y1, x0:x1]
    # A one-pixel edge tolerance includes narrow islands and degenerate UVs.
    signed = []
    for start, end in zip(points, np.roll(points, -1, axis=0), strict=True):
        dx, dy = end - start
        signed.append(
            ((x + 0.5 - start[0]) * dy - (y + 0.5 - start[1]) * dx)
            / max(1e-8, np.hypot(dx, dy))
        )
    mask[y0:y1, x0:x1] |= (np.minimum.reduce(signed) >= -1) | (
        np.maximum.reduce(signed) <= 1
    )


def paint_atlas(image, uv, loops, path):
    size = np.array(image.size)
    pixels = uv[loops] * size
    if pixels.min() < -0.02 or np.max(pixels - size) > 0.02:
        raise ValueError(f"Repeating UV in {image.name}")
    pad = 12
    low = np.maximum(0, np.floor(pixels.min(axis=(0, 1))).astype(int) - pad)
    high = np.minimum(size, np.ceil(pixels.max(axis=(0, 1))).astype(int) + pad + 1)
    width, height = high - low
    keep = np.zeros((height, width), bool)
    for triangle in pixels - low:
        cover_triangle(keep, triangle)
    for axis in (0, 1):
        for _ in range(pad):
            expanded = keep.copy()
            if axis == 0:
                expanded[1:] |= keep[:-1]
                expanded[:-1] |= keep[1:]
            else:
                expanded[:, 1:] |= keep[:, :-1]
                expanded[:, :-1] |= keep[:, 1:]
            keep = expanded
    rgba = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(rgba)
    crop = rgba.reshape(size[1], size[0], 4)[low[1] : high[1], low[0] : high[0], :3]
    if image.is_float or image.colorspace_settings.name != "sRGB":
        raise ValueError(f"Expected a byte sRGB facade image: {image.name}")
    # Blender exposes the original byte image values here. Encoding them a second
    # time visibly lightens the facade after the new PNG is opened.
    encoded = np.clip(crop[keep], 0, 1)
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[keep] = np.rint(encoded * 255).astype(np.uint8)
    write_png(path, rgb)
    index = np.unique(loops)
    uv[index] = (uv[index] * size - low) / (high - low)
    atlas = bpy.data.images.load(str(path))
    atlas.pack()
    atlas.filepath = "//textures/" + path.name
    return atlas


def isolate_materials(model, feature, directory):
    mesh = model.data
    photo_slots = defaultdict(list)
    for index in sorted({face.material_index for face in mesh.polygons}):
        material = mesh.materials[index].copy()
        mesh.materials[index] = material
        material.name = f"{feature['name']}・{index}"
        for node in material.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image:
                photo_slots[node.image].append((index, node))
    layer = mesh.uv_layers.active
    uv = np.empty((len(mesh.loops), 2), dtype=np.float32)
    layer.uv.foreach_get("vector", uv.ravel())
    photos = 0
    for number, (image, slots) in enumerate(photo_slots.items(), 1):
        ids = {index for index, _ in slots}
        loops = np.array(
            [list(f.loop_indices) for f in mesh.polygons if f.material_index in ids]
        )
        atlas = paint_atlas(
            image, uv, loops, directory / f"{feature['id']}-{number}.png"
        )
        atlas.name = f"{feature['name']}・外装写真 {number}"
        for _, node in slots:
            node.image = atlas
            node.id_data.nodes.active = node
        photos += len(loops)
    if photos != feature["imageFaces"]:
        raise ValueError(f"{model.name} photograph coverage changed: {photos}")
    layer.uv.foreach_set("vector", uv.ravel())
    used = {face.material_index for face in mesh.polygons}
    for index in reversed(range(len(mesh.materials))):
        if index not in used:
            mesh.materials.pop(index=index)


def keep_batches(mesh, included):
    editable = bmesh.new()
    try:
        editable.from_mesh(mesh)
        batch = editable.verts.layers.float.get("_BATCHID")
        if batch is None:
            batch = editable.verts.layers.int["_BATCHID"]
        remove = [v for v in editable.verts if int(v[batch]) not in included]
        bmesh.ops.delete(editable, geom=remove, context="VERTS")
        editable.to_mesh(mesh)
        mesh.update()
    finally:
        editable.free()


def extract_buildings(scene, group):
    from omy_facades import repair_facades

    tiles = defaultdict(list)
    for feature in json.loads((HERE / "omy-buildings.json").read_text()):
        tiles[feature["tile"]].append(feature)
    with tempfile.TemporaryDirectory(prefix="omy-atlas-") as temp:
        for tile, features in tiles.items():
            original = scene.objects.get("PLATEAU・" + tile)
            if original is None:  # Cropped builds only import part of the city.
                continue
            source_faces = len(original.data.polygons)
            attribute = original.data.attributes["_BATCHID"]
            ids = {int(value.value) for value in attribute.data}
            split = set()
            for feature in features:
                mesh = original.data.copy()
                keep_batches(mesh, {feature["batch"]})
                if len(mesh.polygons) != feature["faces"]:
                    raise ValueError(f"Unexpected face count for {feature['name']}")
                model = bpy.data.objects.new(feature["name"], mesh)
                group.objects.link(model)
                model.matrix_world = original.matrix_world.copy()
                mesh.name = feature["name"]
                for key, value in feature.items():
                    model[key] = value
                model["source"] = original["source"]
                model["repairedFacadeTriangles"] = repair_facades(model, feature)
                isolate_materials(model, feature, Path(temp))
                center = sum((Vector(p) for p in model.bound_box), Vector()) / 8
                mesh.transform(Matrix.Translation(-center))
                model.matrix_world = model.matrix_world @ Matrix.Translation(center)
                split.add(feature["batch"])
            keep_batches(original.data, ids - split)
            if (
                len(original.data.polygons) + sum(f["faces"] for f in features)
                != source_faces
            ):
                raise ValueError(f"Lost or duplicated faces in {tile}")


def import_model(scene, group, path):
    previous = set(scene.objects)
    bpy.ops.import_scene.gltf(
        filepath=str(path), import_scene_as_collection=False, loglevel=4
    )
    for obj in set(scene.objects) - previous:
        for old in list(obj.users_collection):
            old.objects.unlink(obj)
        group.objects.link(obj)


def export_buildings(group, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    detached = []
    for obj in group.objects:
        obj.select_set(True)
        for material in obj.data.materials:
            tree = material.node_tree
            glazing = tree.nodes.get("PLATEAU · Facade glazing")
            if glazing is None:
                continue
            # glTF cannot encode the Blender normal- and colour-dependent window graph.
            # Its exporter otherwise follows the photo input into the roughness channel.
            for output in glazing.outputs:
                for link in list(output.links):
                    detached.append((tree, output, link.to_socket))
                    tree.links.remove(link)
    try:
        bpy.ops.export_scene.gltf(
            filepath=str(path),
            use_selection=True,
            export_extras=True,
            export_image_format="WEBP",
            export_image_quality=94,
            export_animations=False,
            export_yup=True,
            export_cameras=False,
            export_lights=False,
        )
    finally:
        for tree, output, target in detached:
            tree.links.new(output, target)
        bpy.ops.object.select_all(action="DESELECT")


def save_study(scene, groups, credits, path):
    study = bpy.data.scenes.new("大丸有の建築と東京駅周辺地下街")
    study.world = scene.world
    study.unit_settings.system = "METRIC"
    for group in groups:
        study.collection.children.link(group)
    camera = bpy.data.objects.new("大丸有・建築俯瞰", bpy.data.cameras.new("建築俯瞰"))
    study.collection.objects.link(camera)
    camera.location = (650, -1700, 1250)
    camera.rotation_euler = (
        (Vector((-85, 240, 34)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    )
    camera.data.lens = 44
    camera.data.clip_end = 8000
    study.camera = camera
    study.render.resolution_x = 1600
    study.render.resolution_y = 1000
    study.render.engine = "BLENDER_EEVEE"
    for obj in scene.objects:
        if obj.type == "LIGHT":
            study.collection.objects.link(obj)
    study["caseStudy"] = CASE_STUDY
    study["出典"] = credits.as_string()
    # A partial library write omits the window manager. Blender would reopen it
    # with the default 18 m orbit around the origin, outside the building group.
    import subprocess
    from textwrap import dedent

    initialize_view = dedent("""
        import bpy
        import sys
        from mathutils import Vector

        library_path, output_path = sys.argv[-2:]
        with bpy.data.libraries.load(library_path) as (source, data):
            data.scenes = source.scenes
            data.texts = source.texts
        study = data.scenes[0]
        for text in data.texts:
            text.use_fake_user = True
        window = bpy.context.window_manager.windows[0]
        startup = window.scene
        window.scene = study
        bpy.data.scenes.remove(startup)
        bpy.data.orphans_purge(do_recursive=True)
        camera = study.camera
        focus = Vector((-85, 240, 34))
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type != "VIEW_3D":
                    continue
                space = area.spaces.active
                space.clip_end = camera.data.clip_end
                space.shading.type = "SOLID"
                space.shading.color_type = "TEXTURE"
                space.overlay.show_overlays = False
                orbit = space.region_3d
                orbit.view_location = focus
                orbit.view_distance = (camera.location - focus).length
                orbit.view_rotation = camera.rotation_euler.to_quaternion()
                orbit.view_perspective = "CAMERA"
                orbit.view_camera_zoom = 15
        bpy.context.preferences.filepaths.save_version = 0
        bpy.ops.wm.save_as_mainfile(filepath=output_path, compress=True, relative_remap=False)
    """)
    with tempfile.TemporaryDirectory(prefix="omy-study-") as temp:
        library = Path(temp) / "architecture-library.blend"
        window = bpy.context.window_manager.windows[0]
        previous = window.scene
        window.scene = study
        try:
            bpy.context.view_layer.update()
            bpy.data.libraries.write(
                str(library), {study, credits}, fake_user=True, compress=True
            )
        finally:
            window.scene = previous
            bpy.data.scenes.remove(study)
        subprocess.run(
            [
                bpy.app.binary_path,
                "--background",
                "--factory-startup",
                "--python-exit-code",
                "1",
                "--python-expr",
                initialize_view,
                "--",
                str(library),
                str(Path(path).resolve()),
            ],
            check=True,
        )
