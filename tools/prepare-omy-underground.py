# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.3.5", "mapbox-earcut==1.0.3"]
# ///
"""Convert the UC23-05 Tokyo Station underground CityGML to the city viewer's local GLB."""

import argparse
import hashlib
import json
import math
import struct
import tempfile
import zlib
from array import array
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import urlopen
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import mapbox_earcut
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
URL = "https://assets.cms.plateau.reearth.io/assets/f9/3d8356-9c45-4096-96b5-b52b02a166a4/13101_chiyoda-ku_ubld-mlit_2023_citygml_1_op.zip"
SHA256 = "4f5bca373ea63ac2487cd0170d82a1a691e160d9e49c317684cb0743f56f5682"
GML = "udx/ubld/53394611_ubld_6697_op.gml"
G = "{http://www.opengis.net/gml}"
B = "{http://www.opengis.net/citygml/building/2.0}"
XLINK = "{http://www.w3.org/1999/xlink}href"
METRES = np.array([90528.0, 110953.0])
SEMANTICS = {
    name: role
    for role, names in {
        "floor": ["FloorSurface", "OuterFloorSurface"],
        "ceiling": ["CeilingSurface", "OuterCeilingSurface", "RoofSurface"],
        "wall": ["WallSurface", "InteriorWallSurface"],
        "closure": ["ClosureSurface"],
        "equipment": ["BuildingInstallation", "IntBuildingInstallation"],
    }.items()
    for name in names
}
# Linear base colours distinguish surveyed feature classes in a section view.
PALETTE = {
    "floor": (0.57, 0.49, 0.36),
    "ceiling": (0.64, 0.65, 0.62),
    "wall": (0.28, 0.38, 0.41),
    "closure": (0.42, 0.47, 0.49),
    "stairs": (0.53, 0.36, 0.19),
    "escalator": (0.25, 0.32, 0.35),
    "column": (0.44, 0.47, 0.44),
    "guidance": (0.86, 0.56, 0.10),
    "equipment": (0.32, 0.36, 0.37),
}
REPEAT_METRES = 1.2


def finish_maps(role):
    """Shared seamless maps. Height and tile dimensions are expressed in metres."""
    size = 256
    v, u = (np.indices((size, size)) + 0.5) / size
    floor = role == "floor"
    rows = 2 if floor else 4
    row = np.floor(v * rows).astype(int)
    tile_u = u * 2 + (0 if floor else (row % 2) * 0.5)
    col = np.floor(tile_u).astype(int) % 2
    dx = abs((tile_u + 0.5) % 1 - 0.5) * REPEAT_METRES / 2
    dy = abs((v * rows + 0.5) % 1 - 0.5) * REPEAT_METRES / rows
    # A rounded edge and a slightly recessed joint stay legible at grazing angles.
    edge = np.clip((np.minimum(dx, dy) - 0.0012) / 0.0040, 0, 1)
    edge *= edge * (3 - 2 * edge)
    rng = np.random.default_rng(2005 if floor else 2023)
    grain = rng.normal(0, 1, (size, size))
    tile_tone = np.array([0, 3, -2, 1, 2, -1, 1, -3])[(row * 2 + col)]
    vein = np.sin(2 * np.pi * (2 * u + v) + 0.9 * np.sin(4 * np.pi * v))
    face = 235 + tile_tone + vein * (2.3 if floor else 0.8)
    face += grain * (3.0 if floor else 1.0)
    if floor:
        face -= (grain < -1.6) * 12
    srgb = (186 + grain + (face - 186) * edge)[:, :, None]
    tint = np.array([1.0, 0.995, 0.982]) if floor else np.array([0.986, 0.996, 1.0])
    height = edge * (0.0016 if floor else 0.0019) + grain * 0.000075
    gradients = [
        (np.roll(height, 1, axis=axis) - np.roll(height, -1, axis=axis))
        * size
        / (2 * REPEAT_METRES)
        for axis in (1, 0)
    ]
    # glTF UVs increase downward in image rows; the tangent frame uses +V.
    normals = np.stack([*gradients, np.ones_like(u)], axis=-1)
    normals /= np.linalg.norm(normals, axis=-1, keepdims=True)
    roughness = 218 * (1 - edge) + edge * ((171 if floor else 140) + grain * 2)
    orm = np.stack([np.full_like(u, 255), roughness, np.zeros_like(u)], axis=-1)
    return {
        "base": np.clip(srgb * tint, 0, 255).round().astype("u1"),
        "normal": ((normals * 0.5 + 0.5) * 255).round().astype("u1"),
        "roughness": orm.round().astype("u1"),
    }


def png_bytes(pixels):
    height, width, _ = pixels.shape

    def chunk(kind, contents):
        return (
            struct.pack(">I", len(contents))
            + kind
            + contents
            + struct.pack(">I", zlib.crc32(kind + contents))
        )

    scanlines = b"".join(b"\0" + row.tobytes() for row in pixels)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines, 9))
        + chunk(b"IEND", b"")
    )


def finish_uv(xyz, faces, role):
    if role == "floor":
        return xyz, faces, xyz[:, [0, 2]] / REPEAT_METRES
    # Share a common origin so adjacent material batches retain the same joint phase.
    # Vertices at sharp corners split only when their UV projection changes.
    triangles = xyz[faces]
    normal = np.cross(
        triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    )
    vertical = abs(normal[:, 1]) < 0.65 * np.linalg.norm(normal, axis=1)
    angle = np.arctan2(normal[:, 0], -normal[:, 2])
    direction = np.rint(np.rad2deg(angle)).astype("i8") % 180
    groups = np.where(vertical, direction + 1, 0)
    keys, indices = np.unique(
        faces.astype("i8") * 181 + groups[:, None], return_inverse=True
    )
    points = xyz[keys // 181]
    group = keys % 181
    theta = np.deg2rad(group - 1)
    along = points[:, 0] * np.cos(theta) + points[:, 2] * np.sin(theta)
    uv = (
        np.column_stack(
            (
                np.where(group, along, points[:, 0]),
                np.where(group, -points[:, 1], points[:, 2]),
            )
        )
        / REPEAT_METRES
    )
    return points, indices.reshape(-1, 3), uv.astype("<f4")


def checked_archive(path, url, expected):
    if not path.exists():
        with (
            urlopen(url, timeout=90) as source,
            tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp,
        ):
            while chunk := source.read(1 << 20):
                tmp.write(chunk)
        staged = Path(tmp.name)
        if hashlib.sha256(staged.read_bytes()).hexdigest() != expected:
            raise ValueError("Downloaded source checksum mismatch")
        staged.replace(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"Wrong archive checksum: {path}")
    return ZipFile(path)


def role_for(semantic, name):
    role = SEMANTICS[semantic]
    if role != "equipment":
        return role
    for marker, category in (
        ("stair", "stairs"),
        ("階段", "stairs"),
        ("エスカレータ", "escalator"),
        ("柱", "column"),
        ("TWSI", "guidance"),
        ("wall", "wall"),
        ("壁", "wall"),
    ):
        if marker.lower() in name.lower():
            return category
    return role


def rings(polygon):
    paths = [polygon.find(G + "exterior"), *polygon.findall(G + "interior")]
    return [
        np.fromstring(path.findtext(".//" + G + "posList"), sep=" ").reshape(-1, 3)[:-1]
        for path in paths
    ]


class Part:
    def __init__(self):
        self.points = {}
        self.faces = array("I")
        self.features = set()

    def add(self, xyz, faces, feature):
        local = []
        for point in np.rint(xyz * 1000).astype(np.int32):
            key = tuple(point.tolist())
            local.append(self.points.setdefault(key, len(self.points)))
        for a, b, c in faces:
            triangle = (local[a], local[b], local[c])
            if len(set(triangle)) == 3:
                self.faces.extend(triangle)
                self.features.add(feature)


def triangulate(xyz, ends):
    shell = xyz[: ends[0]]
    normal = np.sum(
        np.cross(shell - shell[0], np.roll(shell, -1, axis=0) - shell[0]), axis=0
    )
    if np.linalg.norm(normal) < 1e-9:
        return []
    if len(xyz) == 3:
        return [(0, 1, 2)]
    plane = np.delete(xyz, int(np.argmax(np.abs(normal))), axis=1)
    indices = mapbox_earcut.triangulate_float64(
        plane, np.array(ends, dtype=np.uint32)
    ).reshape(-1, 3)
    if len(indices):
        a, b, c = xyz[indices[0]]
        if np.dot(np.cross(b - a, c - a), normal) < 0:
            indices = indices[:, ::-1]
    return indices


def collect(archive, registration, origin, geoid):
    angle = math.radians(registration["rotationDegrees"])
    rot = np.array(
        [[math.cos(angle), math.sin(angle)], [-math.sin(angle), math.cos(angle)]]
    )
    shift = registration["translationEastNorth"]
    parts = defaultdict(Part)
    storeys, membership, metadata = {}, {}, {}
    stats = Counter()
    room = None
    with archive.open(GML) as stream:
        for event, elem in ET.iterparse(stream, events=("start", "end")):
            tag = elem.tag.rsplit("}", 1)[-1]
            if event == "start":
                if elem.tag == B + "Room":
                    room = elem.get(G + "id")
                continue
            if tag == "UndergroundBuilding":
                ident = elem.get(G + "id")
                if ident.startswith("grp_"):
                    name = elem.findtext(G + "name")
                    storeys[ident] = name
                    for ref in elem.findall("{*}groupMember"):
                        membership[ref.get(XLINK).removeprefix("#")] = ident
                elem.clear()
                continue
            if elem.tag == B + "Room":
                stats["rooms"] += 1
                room = None
                elem.clear()
                continue
            if elem.tag != B + tag or tag not in SEMANTICS:
                continue
            ident = elem.get(G + "id")
            name = elem.findtext(G + "name", "")
            storey = membership.get(room) or membership.get(ident)
            role = role_for(tag, name)
            part = parts[(tag, role, storey)]
            for polygon in elem.iter(G + "Polygon"):
                boundaries = rings(polygon)
                coord = np.concatenate(boundaries)
                source_xy = (coord[:, [1, 0]] - origin[:2]) * METRES
                xy = source_xy @ rot + shift
                z = (
                    coord[:, 2]
                    + geoid
                    - origin[2]
                    - (xy * xy).sum(axis=1) / (2 * 6371000)
                )
                xyz = np.column_stack((xy[:, 0], z, -xy[:, 1]))
                end = np.cumsum([len(r) for r in boundaries])
                faces = triangulate(xyz, end)
                stats["polygons"] += 1
                stats["holedPolygons"] += int(len(end) > 1)
                if len(faces):
                    part.add(xyz, faces, ident)
                else:
                    stats["zeroAreaPolygons"] += 1
            metadata[ident] = name
            elem.clear()
    return parts, storeys, metadata, stats


def write_glb(parts, storeys, names, stats, context):
    body = bytearray()
    gltf = {
        "asset": {"version": "2.0", "generator": "UC23-05 CityGML / Tokyo Station ENU"},
        "scene": 0,
        "scenes": [{"nodes": []}],
        "nodes": [],
        "meshes": [],
        "materials": [],
        "images": [],
        "textures": [],
        "samplers": [
            {"wrapS": 10497, "wrapT": 10497, "magFilter": 9729, "minFilter": 9987}
        ],
        "buffers": [],
        "bufferViews": [],
        "accessors": [],
        "extras": context,
    }

    def view(data, target=None):
        offset = len(body)
        body.extend(data)
        body.extend(b"\0" * (-len(body) % 4))
        entry = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            entry["target"] = target
        gltf["bufferViews"].append(entry)
        return len(gltf["bufferViews"]) - 1

    for role, color in PALETTE.items():
        gltf["materials"].append(
            {
                "name": "Section | " + role,
                "doubleSided": True,
                "pbrMetallicRoughness": {
                    "baseColorFactor": [*color, 1],
                    "metallicFactor": 0,
                    "roughnessFactor": 0.81,
                },
            }
        )
        if role in ("floor", "wall"):
            material = gltf["materials"][-1]
            material["extras"] = {
                "appearanceSource": "Illustrative procedural finish",
                "repeatMetres": REPEAT_METRES,
            }
            maps = {}
            for channel, pixels in finish_maps(role).items():
                name = f"Illustrative {role} {channel}"
                image = len(gltf["images"])
                gltf["images"].append(
                    {
                        "name": name,
                        "bufferView": view(png_bytes(pixels)),
                        "mimeType": "image/png",
                    }
                )
                maps[channel] = {"index": len(gltf["textures"])}
                gltf["textures"].append({"name": name, "sampler": 0, "source": image})
            material["normalTexture"] = maps["normal"]
            material["pbrMetallicRoughness"].update(
                baseColorTexture=maps["base"],
                metallicRoughnessTexture=maps["roughness"],
                roughnessFactor=1.0,
            )

    def accessor(data, kind, component, target):
        info = {
            "bufferView": view(data.tobytes(), target),
            "count": len(data),
            "type": kind,
            "componentType": component,
        }
        if kind == "VEC3":
            info.update(min=data.min(axis=0).tolist(), max=data.max(axis=0).tolist())
        gltf["accessors"].append(info)
        return len(gltf["accessors"]) - 1

    overall = []
    for (semantic, role, storey), part in parts.items():
        if not part.faces:
            continue
        xyz = np.array(list(part.points), dtype=np.float32) * 0.001
        # Identical solids can be reached through indoor and outdoor BIM surfaces.
        # Within each semantic/storey batch render each triangle once.
        faces = np.asarray(part.faces, dtype=np.uint32).reshape(-1, 3)
        _, first = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
        faces = faces[np.sort(first)]
        attributes = {}
        if role in ("floor", "wall"):
            xyz, faces, uv = finish_uv(xyz, faces, role)
            attributes["TEXCOORD_0"] = accessor(uv, "VEC2", 5126, 34962)
            stats["texturedTriangles"] += len(faces)
        attributes["POSITION"] = accessor(xyz, "VEC3", 5126, 34962)
        dtype, component = (np.uint16, 5123) if len(xyz) <= 65535 else (np.uint32, 5125)
        primitive = {
            "attributes": attributes,
            "indices": accessor(
                faces.astype(dtype).ravel(), "SCALAR", component, 34963
            ),
            "material": list(PALETTE).index(role),
            "mode": 4,
        }
        index = len(gltf["meshes"])
        label = storeys.get(storey, "Building")
        name = f"{label} | {semantic} | {role}"
        extras = {
            "semantic": semantic,
            "role": role,
            "cutawayHide": role in ("ceiling", "closure"),
            "sourceFeatureCount": len(part.features),
        }
        if storey:
            extras.update(storeyId=storey, storeyName=label)
        gltf["nodes"].append({"mesh": index, "name": name, "extras": extras})
        gltf["scenes"][0]["nodes"].append(index)
        gltf["meshes"].append({"name": name, "primitives": [primitive]})
        stats["triangles"] += len(faces)
        stats["vertices"] += len(xyz)
        overall.extend([xyz.min(axis=0), xyz.max(axis=0)])
    stats["meshNodes"] = len(gltf["nodes"])
    stats["sourceFeatureCount"] = len(names)
    context["boundsGltfYUp"] = [
        np.min(overall, axis=0).tolist(),
        np.max(overall, axis=0).tolist(),
    ]
    gltf["buffers"] = [{"byteLength": len(body)}]
    header = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode()
    header += b" " * (-len(header) % 4)
    return (
        struct.pack(
            "<4s4I", b"glTF", 2, 28 + len(header) + len(body), len(header), 0x4E4F534A
        )
        + header
        + struct.pack("<2I", len(body), 0x004E4942)
        + body
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path(tempfile.gettempdir()) / "omy-uc23-underground.zip",
    )
    args = parser.parse_args()
    registration = json.loads(
        (ROOT / "tools/omy-underground-alignment.json").read_text()
    )
    origin = np.array(json.loads((ASSETS / "manifest.json").read_text())["origin"])
    geoid = json.loads((ASSETS / "rail-reference.json").read_text())["verticalDatum"][
        "geoidHeight"
    ]
    with checked_archive(args.archive, URL, SHA256) as archive:
        parts, storeys, names, stats = collect(archive, registration, origin, geoid)
    context = {
        "source": "UC23-05 東京駅周辺地下街LOD4.0（国土交通省、JR東日本コンサルタンツ株式会社）",
        "sourceUrl": URL,
        "archiveSha256": SHA256,
        "citygml": GML,
        "license": "CC BY 4.0",
        "sourceCrs": "EPSG:6697 (JGD2011 latitude, longitude, orthometric height)",
        "originLonLatEllipsoidM": origin.tolist(),
        "geoidHeightM": geoid,
        "gltfAxes": ["east", "up", "south"],
        "registration": {
            "rotationDegrees": registration["rotationDegrees"],
            "translationEastNorth": registration["translationEastNorth"],
        },
        "appearance": "The CityGML contains no Appearance, photographs or measured finishes. Display colours indicate feature classes. Estimated floor and wall finishes use embedded, repeating base colour, normal and metallic-roughness PNGs and world-aligned UVs.",
        "illustrativeFinishes": {
            "texturePeriodM": REPEAT_METRES,
            "floorTileM": [0.60, 0.60],
            "wallTileM": [0.60, 0.30],
            "tileDimensionsMeasured": False,
        },
        "lod": "4.0",
    }
    buffer = write_glb(parts, storeys, names, stats, context)
    with tempfile.NamedTemporaryFile(dir=ASSETS, delete=False) as tmp:
        tmp.write(buffer)
    staged = Path(tmp.name)
    staged.chmod(0o644)
    staged.replace(ASSETS / "omy-underground.glb")
    context.update(
        statistics=dict(stats),
        storeys=storeys,
        glbBytes=len(buffer),
        glbSha256=hashlib.sha256(buffer).hexdigest(),
    )
    (ASSETS / "omy-underground.json").write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(dict(stats), ensure_ascii=False), len(buffer), flush=True)


if __name__ == "__main__":
    main()
