import json
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

SETTINGS = json.loads(Path(__file__).with_name("yaesu-street.json").read_text())
STREET_TILES = SETTINGS["tiles"]
ROAD_LIFT = SETTINGS["roadLift"]
NAMES = {
    "asphalt": "八重洲・都道405号舗装",
    "paint": "八重洲・白色路面標示",
    "gutter": "八重洲・側溝",
    "cover": "八重洲・鉄蓋",
}


def street_material(kind, new_surface):
    existing = bpy.data.materials.get(NAMES[kind])
    if existing:
        return existing
    spec = SETTINGS["surfaces"][kind]
    material, graph, surface = new_surface(
        NAMES[kind], spec["color"], spec["roughness"]
    )
    surface.inputs["Metallic"].default_value = spec["metalness"]
    if kind == "asphalt":
        fine = graph.nodes.new("ShaderNodeTexNoise")
        fine.inputs["Scale"].default_value = 21
        fine.inputs["Detail"].default_value = 2
        coords = graph.nodes.new("ShaderNodeTexCoord")
        graph.links.new(coords.outputs["Object"], fine.inputs["Vector"])
        tones = graph.nodes.new("ShaderNodeValToRGB")
        for stop, level in zip(tones.color_ramp.elements, (0.80, 1.20), strict=True):
            stop.color = (*[level * c for c in spec["color"]], 1)
        graph.links.new(fine.outputs["Fac"], tones.inputs["Fac"])
        graph.links.new(tones.outputs["Color"], surface.inputs["Base Color"])
        bump = graph.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.16
        bump.inputs["Distance"].default_value = 0.008
        graph.links.new(fine.outputs["Fac"], bump.inputs["Height"])
        graph.links.new(bump.outputs["Normal"], surface.inputs["Normal"])
    return material


def apply_street_surface(obj, tile, new_surface):
    mesh = obj.data
    ids = mesh.attributes["_BATCHID"].data
    lookup = STREET_TILES[tile]
    slots = {}
    lifting, omit, drawn = set(), set(), set()
    for face in mesh.polygons:
        kind = lookup.get(str(ids[face.vertices[0]].value))
        if not kind:
            continue
        if kind == "duplicate":
            omit.add(face.index)
            continue
        if kind == "asphalt":
            key = tuple(
                sorted(
                    tuple(round(v * 1000) for v in mesh.vertices[i].co)
                    for i in face.vertices
                )
            )
            if key in drawn:
                omit.add(face.index)
                continue
            drawn.add(key)
        else:
            lifting.update(face.vertices)
        if kind not in slots:
            slots[kind] = len(mesh.materials)
            mesh.materials.append(street_material(kind, new_surface))
        face.material_index = slots[kind]
    if lifting:
        up = obj.matrix_world.to_3x3().inverted() @ Vector((0, 0, ROAD_LIFT))
        for i in lifting:
            mesh.vertices[i].co += up
    if omit:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[i] for i in omit], context="FACES")
        bm.to_mesh(mesh)
        bm.free()
    return len(drawn), len(omit), len(lifting)
