"""Terrain and map layers common to the editable Blender reconstruction."""

import math

import bpy
import numpy as np


def dem_sample(manifest, project, layers, load_image, array_image, geoid):
    samples = []
    for layer in [*layers, manifest["elevation"]]:
        image = load_image(layer["file"])
        image.colorspace_settings.name = "Non-Color"
        pixels = np.rint(array_image(image)[..., :3] * 255).astype(np.int32)
        raw = (pixels[..., 0] << 16) | (pixels[..., 1] << 8) | pixels[..., 2]
        height = np.where(raw >= 0x800000, raw - 0x1000000, raw) * 0.01
        samples.append((layer, height, raw != 0x800000))

    def sample(x, y):
        value = 1.5
        for index, (layer, raster, valid) in enumerate(reversed(samples)):
            rows, columns = raster.shape
            u, v = project(x, y, layer)
            px = np.clip(u * columns - 0.5, 0, columns - 1)
            py = np.clip(v * rows - 0.5, 0, rows - 1)
            i, j = px.astype(int), py.astype(int)
            ni, nj = np.minimum(i + 1, columns - 1), np.minimum(j + 1, rows - 1)
            fx, fy = px - i, py - j
            weighted = (
                (i, j, (1 - fx) * (1 - fy)),
                (ni, j, fx * (1 - fy)),
                (i, nj, (1 - fx) * fy),
                (ni, nj, fx * fy),
            )
            wgt = sum(weight * valid[row, col] for col, row, weight in weighted)
            summed = sum(
                weight * raster[row, col] * valid[row, col]
                for col, row, weight in weighted
            )
            patch = np.divide(summed, np.maximum(wgt, 1e-9))
            edge = np.minimum(np.minimum(u, 1 - u), np.minimum(v, 1 - v))
            smooth = np.clip(edge / 0.028, 0, 1)
            blend = smooth * smooth * (3 - 2 * smooth) * wgt
            if index == 0:
                blend = (edge >= 0) * (wgt > 0)
            value = value + (patch - value) * blend
        return (
            value
            + geoid
            - manifest["origin"][2]
            - (x * x + y * y) / (2 * 6371000)
            - 0.78
        )

    return sample


def world_photos(layers, project, load_image):
    results = []
    for layer in layers:
        u, v = project(0, 0, layer)
        east, north = project(1000, 1000, layer)
        results.append(
            (
                load_image(layer["file"]),
                ((east - u) / 1000, (north - v) / 1000, 0),
                (u, v, 0),
            )
        )
    return results


def project_photos(graph, original, surface, overlays, shade_banks):
    color = original.outputs["Color"]
    nodes, link = graph.nodes, graph.links.new
    geometry = nodes.new("ShaderNodeNewGeometry")

    def operation(operator, left, right):
        node = nodes.new("ShaderNodeMath")
        node.operation = operator
        for i, value in enumerate((left, right)):
            if isinstance(value, (int, float)):
                node.inputs[i].default_value = value
            else:
                link(value, node.inputs[i])
        return node.outputs[0]

    for image, scale, origin in reversed(overlays):
        multiply = nodes.new("ShaderNodeVectorMath")
        multiply.operation = "MULTIPLY"
        multiply.inputs[1].default_value = scale
        link(geometry.outputs["Position"], multiply.inputs[0])
        add = nodes.new("ShaderNodeVectorMath")
        add.operation = "ADD"
        add.inputs[1].default_value = origin
        link(multiply.outputs["Vector"], add.inputs[0])
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = image
        texture.extension = "CLIP"
        texture.label = image.name
        link(add.outputs["Vector"], texture.inputs["Vector"])
        axis = nodes.new("ShaderNodeSeparateXYZ")
        link(add.outputs["Vector"], axis.inputs[0])
        x, y = axis.outputs["X"], axis.outputs["Y"]
        edge_x = operation("MINIMUM", x, operation("SUBTRACT", 1, x))
        edge_y = operation("MINIMUM", y, operation("SUBTRACT", 1, y))
        feather = nodes.new("ShaderNodeMapRange")
        feather.interpolation_type = "SMOOTHSTEP"
        feather.inputs["From Max"].default_value = 0.035
        link(operation("MINIMUM", edge_x, edge_y), feather.inputs["Value"])
        mix = nodes.new("ShaderNodeMixRGB")
        link(
            operation("MULTIPLY", texture.outputs["Alpha"], feather.outputs["Result"]),
            mix.inputs[0],
        )
        link(color, mix.inputs[1])
        patch_color = texture.outputs["Color"]
        if image.name == "park-ground.png":
            world_axes = nodes.new("ShaderNodeSeparateXYZ")
            link(geometry.outputs["Position"], world_axes.inputs[0])
            is_lawn = operation("LESS_THAN", world_axes.outputs["X"], -420)
            tufts = nodes.new("ShaderNodeTexNoise")
            tufts.label = "補正した芝・細かな濃淡"
            tufts.inputs["Scale"].default_value = 6.8
            tufts.inputs["Detail"].default_value = 1.1
            link(geometry.outputs["Position"], tufts.inputs["Vector"])
            grain = nodes.new("ShaderNodeMixRGB")
            grain.blend_type = "MULTIPLY"
            link(is_lawn, grain.inputs[0])
            link(patch_color, grain.inputs[1])
            link(operation("ADD", .78, operation("MULTIPLY", tufts.outputs["Fac"], .44)), grain.inputs[2])
            patch_color = grain.outputs[0]
        link(patch_color, mix.inputs[2])
        color = mix.outputs["Color"]
    if shade_banks:
        axes = nodes.new("ShaderNodeSeparateXYZ")
        link(geometry.outputs["Normal"], axes.inputs[0])
        slope = nodes.new("ShaderNodeMapRange")
        slope.interpolation_type = "SMOOTHSTEP"
        slope.inputs["From Min"].default_value = 0.33
        slope.inputs["From Max"].default_value = 0.76
        link(
            operation("SUBTRACT", 1, operation("ABSOLUTE", axes.outputs["Z"], 0)),
            slope.inputs["Value"],
        )
        mix = nodes.new("ShaderNodeMixRGB")
        mix.inputs[2].default_value = (0.135, 0.125, 0.098, 1)
        link(operation("MULTIPLY", 0.90, slope.outputs["Result"]), mix.inputs[0])
        link(color, mix.inputs[1])
        color = mix.outputs["Color"]
    link(color, surface.inputs["Base Color"])


def sample_axis(extent, fine_from, fine_to):
    point = -extent
    axis = []
    while point < extent:
        axis.append(point)
        if point >= fine_to:
            point = min(extent, point + 16)
        elif point >= fine_from:
            point = min(fine_to, point + 2.5)
        else:
            point = min(fine_from, point + 16)
    return np.asarray([*axis, extent])


def reference_curves(scene, refs, elevation):
    for key, name in (
        ("gsi", "地理院・鉄道中心線"),
        ("n02", "国土数値情報・路線と駅区間"),
    ):
        group = bpy.data.collections.new(f"09  編集用参照・{name}")
        scene.collection.children.link(group)
        group.hide_render = True
        group.hide_viewport = True
        group["data_source"] = str(refs[key].get("source", refs[key].get("sources")))
        curve = bpy.data.curves.new(name, "CURVE")
        curve.dimensions = "3D"
        for feature in refs[key]["features"]:
            points = feature["points"]
            spline = curve.splines.new("POLY")
            spline.points.add(len(points) - 1)
            for point, (x, y) in zip(spline.points, points, strict=True):
                point.co = (x, y, float(elevation(x, y)) + 0.12, 1)
        model = bpy.data.objects.new(name, curve)
        group.objects.link(model)


def tree_placements(layout, elevation, heights, database):
    for index, (x, y, scale, angle) in enumerate(layout):
        overlaps = any(
            math.hypot(east - x, north - y) < max(2.1, radius * 0.8)
            for east, north, radius in database
        )
        if not overlaps:
            yield (x, y, float(elevation(x, y)), scale, angle, heights[index], index)


def import_rail_model(scene, asset):
    group = bpy.data.collections.new("09  レール・東京都レーザ測量とOpenStreetMap")
    scene.collection.children.link(group)
    previous = set(scene.objects)
    bpy.ops.import_scene.gltf(
        filepath=str(asset), import_scene_as_collection=False, loglevel=4
    )
    for model in scene.objects:
        if model in previous:
            continue
        for old in tuple(model.users_collection):
            old.objects.unlink(model)
        group.objects.link(model)
        model["source"] = (
            "© OpenStreetMap contributors / 東京都デジタルツイン実現プロジェクト"
        )
        model["license"] = "https://opendatacommons.org/licenses/odbl/1-0/"
