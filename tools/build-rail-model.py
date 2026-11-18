# /// script
# requires-python = ">=3.11"
# ///
"""Model supported Tokyo Station track sections from OSM geometry and Tokyo laser levels."""

import json
import math
import statistics
import struct
from array import array
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
MATERIALS = [
    {
        "name": "Ballast",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.079, 0.069, 0.059, 1],
            "roughnessFactor": 0.96,
            "metallicFactor": 0,
        },
    },
    {
        "name": "Concrete sleepers",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.25, 0.225, 0.19, 1],
            "roughnessFactor": 0.84,
            "metallicFactor": 0,
        },
    },
    {
        "name": "Steel rails",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.33, 0.285, 0.235, 1],
            "roughnessFactor": 0.46,
            "metallicFactor": 0.58,
        },
    },
    {
        "name": "Rail deck • survey-derived envelope",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.195, 0.165, 0.13, 1],
            "roughnessFactor": 0.92,
            "metallicFactor": 0,
        },
    },
]


def details(survey):
    for track in survey["tracks"]:
        rows = track["points"]
        parts = [[rows[0]]] if rows else []
        for point in rows[1:]:
            previous = parts[-1][-1]
            distance = math.dist(point[:2], previous[:2])
            valid_rise = abs(point[2] - previous[2]) < 0.65 + 0.075 * distance
            if 0.10 < distance <= 28 and valid_rise:
                parts[-1].append(point)
            elif distance >= 0.10:
                parts.append([point])
        for part in parts:
            if len(part) < 2:
                continue
            gauge = track.get("gauge", 1067) / 1000
            filtered = [
                (x, y, statistics.median(row[2] for row in part[max(i - 1, 0) : i + 2]))
                for i, (x, y, _) in enumerate(part)
            ]
            yield track["id"], gauge, filtered


class Mesh:
    def __init__(self):
        self.xyz = array("f")
        self.normals = array("f")
        self.indices = array("I")

    def quad(self, corners):
        a, b, c, _ = corners
        ab = tuple(b[i] - a[i] for i in range(3))
        ac = tuple(c[i] - a[i] for i in range(3))
        n = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        scale = math.sqrt(sum(v * v for v in n))
        if scale < 1e-9:
            return
        first = len(self.xyz) // 3
        for x, y, z in corners:
            self.xyz.extend((x, z, -y))
            self.normals.extend((n[0] / scale, n[2] / scale, -n[1] / scale))
        self.indices.extend((first, first + 1, first + 2, first, first + 2, first + 3))

    def beam(self, left, right, width, depth, ends=False):
        lx, ly, lz = left
        rx, ry, rz = right
        length = math.hypot(rx - lx, ry - ly)
        if length < 0.015:
            return
        x, y = (ly - ry) / length * width / 2, (rx - lx) / length * width / 2
        a, b = (lx - x, ly - y, lz), (rx - x, ry - y, rz)
        c, d = (rx + x, ry + y, rz), (lx + x, ly + y, lz)
        self.quad((a, b, c, d))
        for start, end in ((a, b), (b, c), (c, d), (d, a)):
            if not ends and (start, end) in ((b, c), (d, a)):
                continue
            lower_end = (end[0], end[1], end[2] - depth)
            lower_start = (start[0], start[1], start[2] - depth)
            self.quad((start, lower_start, lower_end, end))


def supported_deck(parts, offset):
    deck = Mesh()
    lower_north = min(point[1] for _, _, points in parts for point in points)
    upper_north = max(point[1] for _, _, points in parts for point in points)
    profiles = []
    for north in range(
        math.ceil(lower_north / 4) * 4, math.floor(upper_north / 4) * 4 + 1, 4
    ):
        hits = []
        for _, _, points in parts:
            for a, b in pairwise(points):
                if a[1] == b[1] or not min(a[1], b[1]) <= north < max(a[1], b[1]):
                    continue
                t = (north - a[1]) / (b[1] - a[1])
                hits.append(
                    (a[0] + (b[0] - a[0]) * t, a[2] + (b[2] - a[2]) * t + offset - 0.11)
                )
        if len(hits) < 2:
            continue
        hits.sort()
        width = hits[-1][0] - hits[0][0]
        if width < 5:
            continue
        bank = 1.65
        samples = [
            (hits[0][0] - bank, hits[0][1]),
            *hits,
            (hits[-1][0] + bank, hits[-1][1]),
        ]
        points = []
        for column in range(29):
            x = samples[0][0] + (samples[-1][0] - samples[0][0]) * column / 28
            left, right = next(
                (a, b)
                for a, b in pairwise(samples)
                if a[0] - 0.001 <= x <= b[0] + 0.001 and b[0] - a[0] > 0.001
            )
            t = (x - left[0]) / (right[0] - left[0])
            points.append((x, north, left[1] + (right[1] - left[1]) * t))
        profiles.append(points)
    for old, new in pairwise(profiles):
        if new[0][1] - old[0][1] > 4.01:
            continue
        for col in range(28):
            deck.quad((old[col], old[col + 1], new[col + 1], new[col]))
        for a, b in ((old[0], new[0]), (new[-1], old[-1])):
            deck.quad((a, b, (b[0], b[1], -2.15), (a[0], a[1], -2.15)))
    return deck


def track_mesh(survey, height_offset):
    ballast, sleepers, steel = (Mesh() for _ in range(3))
    sections = []
    parts = list(details(survey))
    for track_id, gauge, part in parts:
        sections.append(track_id)
        spacing = 0.64
        next_sleeper = spacing / 2
        total = 0
        for a, b in pairwise(part):
            ax, ay, ah = a
            bx, by, bh = b
            az, bz = ah + height_offset, bh + height_offset
            size = math.hypot(bx - ax, by - ay)
            nx, ny = (ay - by) / size, (bx - ax) / size
            ballast.beam((ax, ay, az + 0.025), (bx, by, bz + 0.025), gauge + 1.07, 0.14)
            for distance in (-gauge / 2, gauge / 2):
                dx, dy = nx * distance, ny * distance
                steel.beam(
                    (ax + dx, ay + dy, az + 0.225),
                    (bx + dx, by + dy, bz + 0.225),
                    0.076,
                    0.105,
                )
            while next_sleeper <= total + size:
                weight = (next_sleeper - total) / size
                px, py, pz = (
                    ax + (bx - ax) * weight,
                    ay + (by - ay) * weight,
                    az + (bz - az) * weight + 0.12,
                )
                halfspan = (gauge + 0.66) / 2
                sleepers.beam(
                    (px - nx * halfspan, py - ny * halfspan, pz),
                    (px + nx * halfspan, py + ny * halfspan, pz),
                    0.215,
                    0.092,
                    ends=True,
                )
                next_sleeper += spacing
            total += size
    deck = supported_deck(parts, height_offset)
    return (ballast, sleepers, steel, deck), sections


def export_glb(meshes, sections, source):
    if not sections:
        raise ValueError("No railway runs had continuous survey-backed support")
    body = bytearray()
    views, accessors, primitives = [], [], []

    def put(values, count, target, kind, component):
        raw = values.tobytes()
        view = len(views)
        views.append(
            {
                "buffer": 0,
                "byteOffset": len(body),
                "byteLength": len(raw),
                "target": target,
            }
        )
        body.extend(raw)
        index = len(accessors)
        attrs = {
            "bufferView": view,
            "componentType": component,
            "count": count,
            "type": kind,
        }
        if kind == "VEC3" and target == 34962:
            attrs["min"] = [min(values[i::3]) for i in range(3)]
            attrs["max"] = [max(values[i::3]) for i in range(3)]
        accessors.append(attrs)
        return index

    for number, mesh in enumerate(meshes):
        if not mesh.indices:
            continue
        pos = put(mesh.xyz, len(mesh.xyz) // 3, 34962, "VEC3", 5126)
        normals = put(mesh.normals, len(mesh.normals) // 3, 34962, "VEC3", 5126)
        indices = put(mesh.indices, len(mesh.indices), 34963, "SCALAR", 5125)
        primitives.append(
            {
                "attributes": {"POSITION": pos, "NORMAL": normals},
                "indices": indices,
                "material": number,
            }
        )
    gltf = {
        "asset": {
            "version": "2.0",
            "generator": "Tokyo Station railway reconstruction",
        },
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "Tokyo Station surveyed rail corridor"}],
        "meshes": [{"primitives": primitives}],
        "materials": MATERIALS,
        "bufferViews": views,
        "accessors": accessors,
        "buffers": [{"byteLength": len(body)}],
        "extras": {
            "survey": source,
            "trackIds": sorted(set(sections)),
            "source": "© OpenStreetMap contributors; Tokyo Metropolitan Government airborne laser survey",
            "license": "https://opendatacommons.org/licenses/odbl/1-0/",
        },
    }
    header = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode()
    header += b" " * (-len(header) % 4)
    size = 12 + 8 + len(header) + 8 + len(body)
    return (
        struct.pack("<4s4I", b"glTF", 2, size, len(header), 0x4E4F534A)
        + header
        + struct.pack("<2I", len(body), 0x004E4942)
        + body
    )


def main():
    survey = json.loads((ASSETS / "survey-rail.json").read_text())
    reference = json.loads((ASSETS / "rail-reference.json").read_text())
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    offset = reference["verticalDatum"]["geoidHeight"] - manifest["origin"][2]
    meshes, sections = track_mesh(survey, offset)
    (ASSETS / "rail-model.glb").write_bytes(
        export_glb(meshes, sections, survey.get("source"))
    )
    print(
        f"Railway mesh: {len(set(sections))} tracks with survey support; {sum(len(m.indices) // 3 for m in meshes):,} triangles"
    )


if __name__ == "__main__":
    main()
