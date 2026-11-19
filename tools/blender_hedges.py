import json
from pathlib import Path

import bpy

SETTINGS = json.loads(Path(__file__).with_name("yaesu-hedges.json").read_text())
HEDGE_TILE = SETTINGS["tile"]


def apply_hedge_surface(obj, new_surface):
    material, graph, surface = new_surface(
        "八重洲・刈込低木", SETTINGS["leafColor"], 0.94
    )
    texture = bpy.data.images["Street foliage · leaf field"]
    leaves = graph.nodes.new("ShaderNodeTexImage")
    leaves.image = texture
    multiply = graph.nodes.new("ShaderNodeMixRGB")
    multiply.blend_type = "MULTIPLY"
    multiply.inputs[0].default_value = 1.0
    multiply.inputs[2].default_value = (*SETTINGS["leafColor"], 1.0)
    graph.links.new(leaves.outputs["Color"], multiply.inputs[1])
    graph.links.new(multiply.outputs[0], surface.inputs["Base Color"])
    mesh = obj.data
    ids = mesh.attributes["_BATCHID"].data
    material_index = len(mesh.materials)
    mesh.materials.append(material)
    batches = set(SETTINGS["batches"])
    uv = mesh.uv_layers.active.uv
    for face in mesh.polygons:
        batch = ids[face.vertices[0]].value
        if batch not in batches:
            continue
        face.material_index = material_index
        points = [obj.matrix_world @ mesh.vertices[i].co for i in face.vertices]
        normal = (points[1] - points[0]).cross(points[2] - points[0]).normalized()
        top = abs(normal.z) > 0.62
        faces_east = abs(normal.x) > abs(normal.y)
        for i, (east, north, up) in zip(face.loop_indices, points, strict=True):
            a = east if top or not faces_east else north
            b = north if top else up
            scale = SETTINGS["repeatsPerMeter"]
            uv[i].vector = (
                (0.932 * a - 0.362 * b) * scale + batch * 0.37,
                (0.362 * a + 0.932 * b) * scale + batch * 0.19,
            )
