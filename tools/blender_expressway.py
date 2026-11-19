import json
from pathlib import Path

import bpy

TOOLS = Path(__file__).resolve().parent
deck_settings = json.loads((TOOLS / "nihonbashi-expressway.json").read_text())
MATERIAL_NAME = "日本橋川・首都高速舗装"


def deck_material(new_surface):
    existing = bpy.data.materials.get(MATERIAL_NAME)
    if existing:
        return existing
    material, graph, surface = new_surface(MATERIAL_NAME, (0.11, 0.11, 0.11), 0.91)
    image = graph.nodes.new("ShaderNodeTexImage")
    image.image = bpy.data.images.load(
        str(TOOLS.parent / "assets" / deck_settings["file"]), check_existing=True
    )
    image.extension = "EXTEND"
    coords = graph.nodes.new("ShaderNodeUVMap")
    coords.uv_map = MATERIAL_NAME
    graph.links.new(coords.outputs["UV"], image.inputs["Vector"])
    graph.links.new(image.outputs["Color"], surface.inputs["Base Color"])
    return material


def apply_deck_surface(obj, tile, new_surface):
    selected = set(deck_settings["tiles"][tile])
    west, south, east, north = deck_settings["bounds"]
    mesh = obj.data
    ids = mesh.attributes["_BATCHID"].data
    bridge = "_brid_" in tile
    target = []
    for face in mesh.polygons:
        if ids[face.vertices[0]].value not in selected:
            continue
        points = [obj.matrix_world @ mesh.vertices[i].co for i in face.vertices]
        normal = (points[1] - points[0]).cross(points[2] - points[0]).normalized()
        elevated = not bridge or all(
            p.z >= deck_settings["bridgeDeckHeight"] for p in points
        )
        xx, yy, _ = zip(*points, strict=True)
        at_river = (
            max(xx) >= west
            and min(xx) <= east
            and max(yy) >= south
            and min(yy) <= north
        )
        if elevated and at_river and normal.z > deck_settings["minimumUpNormal"]:
            target.append((face.index, points))
    if not target:
        return 0
    slot = len(mesh.materials)
    mesh.materials.append(deck_material(new_surface))
    active = mesh.uv_layers.active
    uv = mesh.uv_layers.new(name=MATERIAL_NAME)
    if active:
        mesh.uv_layers.active = active
    for index, points in target:
        face = mesh.polygons[index]
        face.material_index = slot
        for corner, point in zip(face.loop_indices, points, strict=True):
            uv.uv[corner].vector = (
                (point.x - west) / (east - west),
                (point.y - south) / (north - south),
            )
    return len(target)
