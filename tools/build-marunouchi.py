# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = []
#
# [tool.blender]
# version = "5.2"
# ///
"""Build the freestanding Marunouchi 2-chome exterior detail GLB in Blender."""

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
TAU = 2 * math.pi
WEST = ((-295.560, -172.604), (-270.153, -75.376), 15)
NORTH = ((-270.153, -75.376), (-235.924, -84.413), 5)


def surface(name, rgb, rough, metal=0.0, image=None):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*rgb, 1)
    mat.use_nodes = True
    graph = mat.node_tree
    bsdf = next(n for n in graph.nodes if n.type == "BSDF_PRINCIPLED")
    for key, value in [
        ("Base Color", (*rgb, 1)),
        ("Roughness", rough),
        ("Metallic", metal),
    ]:
        bsdf.inputs[key].default_value = value
    if image:
        tex = graph.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(str(image))
        tex.image.pack()
        graph.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


class Part:
    def __init__(self, name, xy, direction, outward):
        self.name = name
        self.base, self.direction, self.outward = map(Vector, (xy, direction, outward))
        self.vertices, self.faces, self.slots, self.uvs = [], [], [], []
        self.materials = {}

    def face(self, vertices, material, uv=None):
        start = len(self.vertices)
        for s, t, z in vertices:
            x, y = self.base + s * self.direction + t * self.outward
            self.vertices.append((x, y, z))
        self.faces.append(list(range(start, len(self.vertices))))
        self.slots.append(self.materials.setdefault(material, len(self.materials)))
        self.uvs.extend(uv or [(0, 0)] * len(vertices))

    def box(self, bounds, material, top=None, top_uv=None, facade_uv=None):
        s0, t0, z0, s1, t1, z1 = bounds
        p = [
            (s0, t0, z0),
            (s1, t0, z0),
            (s1, t1, z0),
            (s0, t1, z0),
            (s0, t0, z1),
            (s1, t0, z1),
            (s1, t1, z1),
            (s0, t1, z1),
        ]
        for ids in (
            (3, 2, 1, 0),
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
            (4, 5, 6, 7),
        ):
            corners = [p[i] for i in ids]
            on_top = ids[0] == 4
            uv = (
                [top_uv(*v[:2]) for v in corners]
                if on_top and top_uv
                else [(s + t, z) for s, t, z in corners]
            )
            if facade_uv:
                uv = [facade_uv(s, z) for s, _, z in corners]
            self.face(corners, top if on_top and top else material, uv)

    def finish(self):
        mesh = bpy.data.meshes.new(self.name)
        mesh.from_pydata(self.vertices, [], self.faces)
        mesh.update()
        for mat in self.materials:
            mesh.materials.append(mat)
        mesh.polygons.foreach_set("material_index", self.slots)
        uv = mesh.uv_layers.new(name="Surface")
        uv.uv.foreach_set("vector", [a for pt in self.uvs for a in pt])
        obj = bpy.data.objects.new(self.name, mesh)
        bpy.context.collection.objects.link(obj)
        return obj


def wall(name, data, materials):
    a, b, bays = data
    vec = Vector(b) - Vector(a)
    length = vec.length
    along = vec.normalized()
    part = Part(name, a, along, (-along.y, along.x))
    pitch = length / bays
    stone, trim = materials["stone"], materials["trim"]
    glass = materials["glass"]["north" if name.endswith("north") else "west"]

    def glazing_uv(s, z):
        return s / length, (z - 9.68) / 22.08

    # The straight runs in the PLATEAU west photograph repeat at ~6.7 m horizontally
    # and 3.25 m vertically. The low shopfront is obscured and is left in the source model.
    for row in range(7):
        center = 10.33 + 3.25 * row
        low, high = center - 0.59, center + 0.59
        for j in range(bays * 3):
            start, end = j * pitch / 3, (j + 1) * pitch / 3
            part.box((start + 0.014, 0.02, low, end - 0.014, 0.145, high), stone)
        part.box((0.01, 0.04, high - 0.055, length - 0.01, 0.176, high + 0.027), trim)
        top = low + 3.25
        for j in range(bays):
            start = j * pitch
            if row == 6:
                top = 31.70
            # The prominent pier and two slender glazing bars are distinct profiles.
            part.box((start + 0.012, 0.025, high, start + 0.19, 0.157, top), trim)
            for k in range(3):
                left = start + k * pitch / 3 + (0.19 if k == 0 else 0.035)
                right = start + (k + 1) * pitch / 3 - 0.015
                part.box(
                    (left, 0.027, high + 0.025, right, 0.053, top - 0.02),
                    glass,
                    facade_uv=glazing_uv,
                )
                if k:
                    part.box(
                        (
                            start + k * pitch / 3 - 0.013,
                            0.025,
                            high,
                            start + k * pitch / 3 + 0.034,
                            0.110,
                            top,
                        ),
                        trim,
                    )
    o = part.finish()
    o["source"] = "PLATEAU 2025 Chiyoda chiyoda-data454 / batch 1, upper street faces"
    o["detail"] = (
        "Sill and bay positions from rectified source atlas; profile depths and material finishes reconstructed."
    )
    return o


def ring(part, center, z0, z1, r0, r1, mat, segments=64):
    x, y = center

    def v(i, r, z):
        a = i * TAU / segments
        return x + r * math.cos(a), y + r * math.sin(a), z

    for i in range(segments):
        j = i + 1
        part.face([v(i, r1, z0), v(j, r1, z0), v(j, r1, z1), v(i, r1, z1)], mat)
        part.face([v(i, r0, z1), v(i, r1, z1), v(j, r1, z1), v(j, r0, z1)], mat)
        if r0 > 0:
            part.face([v(i, r0, z1), v(j, r0, z1), v(j, r0, z0), v(i, r0, z0)], mat)


def rooftop(scan, m):
    origin, u, v = scan["origin"], scan["u"], scan["v"]
    shelf = Part("Marunouchi 2-chome - plant housings", origin, u, v)
    lo, bt, hi, tp = scan["crop"]

    def uv(s, t):
        return (s - lo) / (hi - lo), (t - bt) / (tp - bt)

    for item in scan["plinths"]:
        s0, t0, s1, t1 = item["bounds"]
        z = item["z"]
        shelf.box((s0, t0, 39.73, s1, t1, z), m["metal"], m["roof"], uv)
        for side in (s0, s1 - 0.062):
            shelf.box((side, t0, z - 0.06, side + 0.062, t1, z + 0.014), m["edge"])
        for side in (t0, t1 - 0.062):
            shelf.box((s0, side, z - 0.06, s1, side + 0.062, z + 0.014), m["edge"])
        # Recessed cladding joints reflect the modular fabrication; no pipe route or
        # specific HVAC manufacturer is asserted by the airborne point sample.
        count = round((t1 - t0) / 1.65)
        for j in range(1, count):
            t = t0 + (t1 - t0) * j / count
            for s in (s0 - 0.014, s1):
                shelf.box(
                    (s, t - 0.014, 39.80, s + 0.018, t + 0.014, z - 0.06), m["seam"]
                )
    base = shelf.finish()
    base["source"] = (
        "Tokyo airborne laser survey 09LD1884 and 20 cm orthophoto; heights are median LAS returns."
    )
    cover = Part("Marunouchi 2-chome - surveyed circular rooftop units", origin, u, v)
    for item in scan["fans"]:
        x, y = item["center"]
        r, z = item["radius"], item["z"]
        ring(cover, (x, y), 41.765, z - 0.06, r - 0.13, r, m["edge"])
        ring(cover, (x, y), z - 0.20, z - 0.16, 0, r - 0.12, m["well"])
        # Airborne data resolves the shroud. Vanes and the protective guard below
        # the measured rim are illustrative and remain editable geometry.
        for i in range(9):
            a = TAU * i / 9
            blade = [
                (r * 0.18, a, z - 0.105),
                (r * 0.82, a + 0.25, z - 0.09),
                (r * 0.83, a + 0.61, z - 0.145),
                (r * 0.2, a + 0.38, z - 0.14),
            ]
            cover.face(
                [
                    (x + radius * math.cos(theta), y + radius * math.sin(theta), height)
                    for radius, theta, height in blade
                ],
                m["blade"],
            )
        ring(cover, (x, y), z - 0.14, z - 0.08, 0, r * 0.19, m["metal"])
        for k in range(2, 9):
            radius = r * k / 9
            ring(
                cover,
                (x, y),
                z - 0.052,
                z - 0.025,
                radius - 0.014,
                radius + 0.014,
                m["grille"],
                48,
            )
        for i in range(10):
            a = i * TAU / 10
            d = Vector((math.cos(a), math.sin(a)))
            n = Vector((-d.y, d.x)) * 0.017
            start = Vector((x, y)) + r * 0.18 * d
            end = Vector((x, y)) + (r - 0.09) * d
            cover.face(
                [(*p, z - 0.012) for p in (start - n, end - n, end + n, start + n)],
                m["grille"],
            )
        ring(cover, (x, y), z - 0.06, z, r - 0.12, r + 0.015, m["grille"])
    o = cover.finish()
    o["source"] = (
        "Two raised circular features registered independently in Tokyo 2024 LAS and orthophotography."
    )
    o["returns"] = sum(f["returns"] for f in scan["fans"])


def build(work, output):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from marunouchi_glazing import recover

    recover(ROOT, work, {"west": WEST, "north": NORTH})
    scan = json.loads((work / "roof.json").read_text())
    mats = {
        "stone": surface(
            "Marunouchi - warm grey spandrel aggregate",
            (0.36, 0.34, 0.31),
            0.78,
            image=work / "spandrel.png",
        ),
        "trim": surface(
            "Marunouchi - bronze aluminium glazing profiles",
            (0.24, 0.226, 0.20),
            0.32,
            0.64,
        ),
        "glass": {
            side: surface(
                f"Marunouchi - measured glazing {side}",
                (0.16, 0.19, 0.20),
                0.22,
                0.30,
                image=work / f"glass-{side}.png",
            )
            for side in ("west", "north")
        },
        "roof": surface(
            "Marunouchi - local orthophoto equipment tops",
            (0.36, 0.36, 0.36),
            0.68,
            image=work / "equipment.png",
        ),
        "metal": surface(
            "Marunouchi - sheet metal housing", (0.31, 0.325, 0.32), 0.55, 0.33
        ),
        "seam": surface(
            "Marunouchi - sheet metal seam", (0.15, 0.16, 0.16), 0.63, 0.24
        ),
        "edge": surface(
            "Marunouchi - folded metal edge", (0.35, 0.36, 0.35), 0.43, 0.52
        ),
        "grille": surface(
            "Marunouchi - galvanised wire guard", (0.195, 0.214, 0.209), 0.48, 0.60
        ),
        "well": surface("Marunouchi - fan recess", (0.028, 0.033, 0.032), 0.87),
        "blade": surface("Marunouchi - fan vanes", (0.105, 0.126, 0.126), 0.46, 0.43),
    }
    wall("Marunouchi 2-chome - Nakadori west", WEST, mats)
    wall("Marunouchi 2-chome - cross street north", NORTH, mats)
    rooftop(scan, mats)
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(output), export_format="GLB", export_yup=True, export_extras=True
    )
    print("Marunouchi exterior", output, output.stat().st_size)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "assets/marunouchi-nichome.glb"
    )
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    build(args.work, args.output)
