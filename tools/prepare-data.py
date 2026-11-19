# /// script
# requires-python = ">=3.11"
# dependencies = ["DracoPy==2.2.0", "numpy==2.4.4", "pillow==13.0.0"]
# ///
"""Cut official PLATEAU and GSI layers around Tokyo Station."""
import argparse
import gzip
import hashlib
import json
import math
import struct
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import urlopen

from facade_atlas import FACADES, repair_facade

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
CACHE = Path(tempfile.gettempdir()) / "tokyo-flight-data" / "http"
ORIGIN = (139.76610, 35.68117, 40.0)
API = "https://api.plateauview.mlit.go.jp/datacatalog/3dtiles/"
LAYERS = {
    "chiyoda": ("13101", "bldg-lod2-texture", "building"),
    "chuo": ("13102", "bldg-lod2-texture", "building"),
    "minato": ("13103", "bldg-lod2-texture", "building"),
    "roads": ("13101", "tran-lod3", "road"),
    "chuo-roads": ("13102", "tran-lod3", "road"),
    "water": ("13101", "wtr-lod1", "water"),
    "vegetation": ("13101", "veg-lod3", "grass"),
}
YEAR = 2025
DATASETS = "https://api.plateauview.mlit.go.jp/datacatalog/plateau-datasets"
# Local ENU (east, south) metres. Street-scale furniture is visible on the station and moat approaches.
FURNITURE_VIEWS = ((-110, -5, 255), (-460, 55, 160), (145, 25, 165))
CITY_VIEW = ((0, 0, 1950),)

def get(url):
    key = CACHE / hashlib.sha256(url.encode()).hexdigest()
    if key.exists():
        return key.read_bytes()
    for attempt in range(3):
        try:
            with urlopen(url, timeout=25) as response:
                data = response.read()
            key.write_bytes(data)
            return data
        except OSError:
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))

def tiles(layer):
    name, (ward, spec, kind) = layer
    catalog = f"{API}{ward}-{spec}-{YEAR}/tileset.json"
    lon, lat = map(math.radians, ORIGIN[:2])
    east_scale = 6386400 * math.cos(lat)
    north_scale = 6359500
    radius = 2650 if kind == "building" else 1950

    def visit(tile, url):
        west, south, east, north, *_ = tile["boundingVolume"]["region"]
        nearest_x = max((west-lon)*east_scale, (lon-east)*east_scale, 0)
        nearest_y = max((south-lat)*north_scale, (lat-north)*north_scale, 0)
        distance = math.hypot(nearest_x, nearest_y)
        if distance > radius:
            return
        children = tile.get("children", ())
        content = tile.get("content", {}).get("uri")
        size = max((east-west)*east_scale, (north-south)*north_scale)
        coarse_limit = 420 if kind == "building" else 280
        use_parent = content and distance > 1140 and size <= coarse_limit
        if content and content.endswith(".json"):
            nested = urljoin(url, content)
            yield from visit(json.loads(get(nested))["root"], nested)
        elif children and not use_parent:
            for child in children:
                yield from visit(child, url)
        elif content:
            yield (name, kind, urljoin(url, content))

    root = json.loads(get(catalog))["root"]
    return catalog, list(visit(root, catalog))

def unpack(entry):
    name, kind, url = entry
    data = get(url)
    if data[:4] == b"b3dm":
        _, version, total, ft_json, ft_bin, bt_json, bt_bin = struct.unpack_from("<4s6I", data)
        assert version == 1 and total == len(data), url
        table = json.loads(data[28:28 + ft_json]) if ft_json else {}
        center = table.get("RTC_CENTER", (0, 0, 0))
        data = data[28 + ft_json + ft_bin + bt_json + bt_bin:]
    else:
        center = (0, 0, 0)
    assert data[:4] == b"glTF", url

    header = struct.unpack_from("<4s4I", data)
    json_end = 20 + header[3]
    gltf = json.loads(data[20:json_end])
    gltf_center = gltf.get("extensions", {}).get("CESIUM_RTC", {}).get("center", (0, 0, 0))
    center = [a + b for a, b in zip(center, gltf_center)]
    gltf.get("extensions", {}).pop("CESIUM_RTC", None)
    gltf["extensionsUsed"] = [ext for ext in gltf.get("extensionsUsed", []) if ext != "CESIUM_RTC"]
    gltf["extensionsRequired"] = [ext for ext in gltf.get("extensionsRequired", []) if ext != "CESIUM_RTC"]
    tile_id = f"{name}-{Path(url).stem}"
    data = resize_textures(gltf, data[json_end+8:], center, kind, tile_id)
    return {"id": tile_id, "kind": kind, "center": center, "source": url}, data

def resize_textures(gltf, binary, center, kind, tile_id=None):
    lon, lat = map(math.radians, ORIGIN[:2])
    n = 6378137 / math.sqrt(1 - .00669438 * math.sin(lat)**2)
    base = (n*math.cos(lat)*math.cos(lon), n*math.cos(lat)*math.sin(lon), n*.99330562*math.sin(lat))
    distance = math.dist(center, base)
    width = 1536 if distance < 490 else 896 if distance < 1080 else 512 if distance < 1900 else 320
    caps = {"building": width, "bridge": 512, "furniture": 384, "tree": 384, "grass-detail": 384}
    width = min(width, caps.get(kind, width))
    image_views = {image["bufferView"]: image["mimeType"] for image in gltf.get("images", [])}
    body = bytearray()
    for i, view in enumerate(gltf["bufferViews"]):
        start = view.get("byteOffset", 0)
        chunk = binary[start:start + view["byteLength"]]
        if tile_id in FACADES and i in image_views:
            chunk = repair_facade(tile_id, chunk)
        elif kind in caps and i in image_views and len(chunk) > 9000:
            codec = {"image/webp": "webp", "image/jpeg": "jpeg", "image/png": "png"}[image_views[i]]
            resized = subprocess.run(
                ["magick", "-limit", "thread", "1", f"{codec}:-", "-resize", f"{width}x{width}>",
                 "-quality", "84", f"{codec}:-"],
                input=chunk, stdout=subprocess.PIPE, check=True,
            ).stdout
            if len(resized) < len(chunk):
                chunk = resized
        view.update(byteOffset=len(body), byteLength=len(chunk))
        body.extend(chunk)
        body.extend(b"\0" * (-len(body) % 4))
    gltf["buffers"][0]["byteLength"] = len(body)
    heading = json.dumps(gltf, separators=(",", ":")).encode()
    heading += b" " * (-len(heading) % 4)
    return (struct.pack("<4s4I", b"glTF", 2, 28 + len(heading) + len(body), len(heading), 0x4e4f534a)
            + heading + struct.pack("<2I", len(body), 0x004e4942) + body)

def image_tiles(layer, zoom, radius, extension, filename):
    def xy(lon, lat):
        columns = 2**zoom
        return ((lon + 180) / 360 * columns,
                (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * columns)

    lon, lat, _ = ORIGIN
    x0, y0 = xy(lon-radius/90300, lat+radius/111000)
    x1, y1 = xy(lon+radius/90300, lat-radius/111000)
    left, top, right, bottom = math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1)
    grid = [(x, y) for y in range(top, bottom) for x in range(left, right)]
    with tempfile.TemporaryDirectory(prefix="tokyo-mosaic-") as temp:
        def save_tile(cell):
            x, y = cell
            path = Path(temp) / f"{x}-{y}.{extension}"
            data = get(f"https://cyberjapandata.gsi.go.jp/xyz/{layer}/{zoom}/{x}/{y}.{extension}")
            path.write_bytes(data)
            return str(path)

        with ThreadPoolExecutor(max_workers=16) as pool:
            paths = list(pool.map(save_tile, grid))
        command = ["magick", "montage", "-font", "/System/Library/Fonts/Helvetica.ttc", *paths, "-tile", f"{right-left}x{bottom-top}",
                   "-geometry", "+0+0", "-background", "none", "-quality", "90", str(ASSETS / filename)]
        subprocess.run(command, check=True)
    return {"zoom": zoom, "bounds": [left, top, right, bottom], "file": filename}

ARCHIVE_LIMIT = 48 * 1024 * 1024

def pack_archives(entries, prefix="city", start=0):
    records, archives = [], []
    part = bytearray()

    def flush():
        filename = f"{prefix}-{len(archives):02d}.bin.gz"
        target = ASSETS / filename
        staging = target.with_suffix(".new")
        staging.write_bytes(gzip.compress(part, compresslevel=4, mtime=0))
        staging.replace(target)
        archives.append(filename)
        part.clear()

    for index, (record, glb) in enumerate(entries, 1):
        if part and len(part) + len(glb) > ARCHIVE_LIMIT:
            flush()
        record.update(archive=start + len(archives), offset=len(part), length=len(glb))
        part.extend(glb)
        records.append(record)
        if index % 60 == 0:
            print(f"Packed {index} tiles", flush=True)
    if part:
        flush()
    return records, archives


def region_intersects(tile, views):
    west, south, east, north = map(math.degrees, tile["boundingVolume"]["region"][:4])
    x0, x1 = [(lon - ORIGIN[0]) * 90528 for lon in (west, east)]
    z0, z1 = [(ORIGIN[1] - lat) * 110953 for lat in (north, south)]
    return any(math.hypot(max(x0 - x, x - x1, 0), max(z0 - z, z - z1, 0)) <= radius
               for x, z, radius in views)


def detail_urls(tile, url, views):
    if not region_intersects(tile, views):
        return
    uri = tile.get("content", {}).get("uri")
    if uri and uri.endswith(".json"):
        source = urljoin(url, uri)
        yield from detail_urls(json.loads(get(source))["root"], source, views)
    elif tile.get("children"):
        for child in tile["children"]:
            yield from detail_urls(child, url, views)
    elif uri:
        yield urljoin(url, uri)


def batch_table(data):
    ft, fb, bt, _ = struct.unpack_from("<4I", data, 12)
    start = 28 + ft + fb
    return json.loads(data[start:start + bt])


def local_xz(points, center):
    import numpy as np

    lon, lat = map(math.radians, ORIGIN[:2])
    cl, sl, cp, sp = math.cos(lon), math.sin(lon), math.cos(lat), math.sin(lat)
    radius = 6378137 / math.sqrt(1 - .00669437999014 * sp**2)
    height = ORIGIN[2]
    base = [(radius + height) * cp * cl, (radius + height) * cp * sl,
            (radius * (1 - .00669437999014) + height) * sp]
    # The archive stores glTF Y-up axes; 3D Tiles rotates these into Earth-fixed Z-up.
    ecef = points[:, [0, 2, 1]].astype(float) * [1, -1, 1] + (np.asarray(center) - base)
    return ecef @ np.array([[-sl, sp * cl], [cl, sp * sl], [0, -cp]])


def clip_detail(glb, center, table, seen, views, trees):
    """Retain faces inside the view footprint, once per official GML feature across all wards."""
    import DracoPy
    import numpy as np

    json_end = 20 + struct.unpack_from("<I", glb, 12)[0]
    gltf = json.loads(glb[20:json_end])
    if any(set(node) != {"mesh"} for node in gltf["nodes"]):
        raise ValueError("Unexpected transformed node in PLATEAU detail tile")
    binary = glb[json_end + 8:]
    old_views, old_accessors = gltf["bufferViews"], gltf["accessors"]
    gltf["bufferViews"], gltf["accessors"] = [], []
    body = bytearray()
    used = set()
    tree_bounds = {}
    ids = table["gml_id"]
    fresh = np.array([feature not in seen for feature in ids])
    scalar_types = {5121: "u1", 5123: "<u2", 5125: "<u4", 5126: "<f4"}

    def read_view(i):
        view = old_views[i]
        start = view.get("byteOffset", 0)
        return binary[start:start + view["byteLength"]]

    def add_view(data):
        offset = len(body)
        body.extend(data)
        body.extend(b"\0" * (-len(body) % 4))
        gltf["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": len(data)})
        return len(gltf["bufferViews"]) - 1

    def accessor(old, values=None):
        desc = dict(old_accessors[old])
        if values is not None:
            desc["bufferView"] = add_view(values.astype(scalar_types[desc["componentType"]]).tobytes())
            desc["count"] = len(values)
            for key, fn in (("min", np.min), ("max", np.max)):
                if key in desc:
                    desc[key] = np.atleast_1d(fn(values, axis=0)).tolist()
        gltf["accessors"].append(desc)
        return len(gltf["accessors"]) - 1

    for image in gltf.get("images", []):
        image["bufferView"] = add_view(read_view(image["bufferView"]))
    for mesh in gltf["meshes"]:
        kept = []
        for prim in mesh["primitives"]:
            draco = prim["extensions"]["KHR_draco_mesh_compression"]
            packed = read_view(draco["bufferView"])
            decoded = DracoPy.decode(packed)
            attributes = {name: decoded.get_attribute_by_unique_id(i)["data"]
                          for name, i in draco["attributes"].items()}
            batches = attributes["_BATCHID"].ravel().astype(int)
            faces = decoded.faces
            batch = batches[faces[:, 0]]
            xz = local_xz(attributes["POSITION"], center)
            bounds = xz[faces]
            low, high = bounds.min(axis=1), bounds.max(axis=1)
            visible = np.zeros(len(faces), dtype=bool)
            for x, z, radius in views:
                delta = np.maximum(np.maximum(low - [x, z], [x, z] - high), 0)
                visible |= (delta * delta).sum(axis=1) <= radius**2
            mask = fresh[batch] & visible
            if not mask.any():
                continue
            retained = np.unique(batch[mask])
            used.update(retained.tolist())
            if trees is not None:
                for number in retained:
                    footprint = xz[batches == number]
                    left, right = footprint.min(axis=0), footprint.max(axis=0)
                    if number in tree_bounds:
                        left = np.minimum(left, tree_bounds[number][0])
                        right = np.maximum(right, tree_bounds[number][1])
                    tree_bounds[number] = (left, right)
            if mask.all():
                draco["bufferView"] = add_view(packed)
                prim["indices"] = accessor(prim["indices"])
                prim["attributes"] = {k: accessor(i) for k, i in prim["attributes"].items()}
            else:
                vertices, indices = np.unique(faces[mask].ravel(), return_inverse=True)
                prim["indices"] = accessor(prim["indices"], indices)
                prim["attributes"] = {k: accessor(i, attributes[k][vertices])
                                      for k, i in prim["attributes"].items()}
                del prim["extensions"]["KHR_draco_mesh_compression"]
            kept.append(prim)
        mesh["primitives"] = kept
    if not used:
        return None, []
    if trees is not None:
        for left, right in tree_bounds.values():
            x, z = (left + right) / 2
            trees.append([round(float(x), 2), round(float(-z), 2), round(float(max(right - left) / 2), 2)])
    return resize_textures(gltf, body, center, "detail"), sorted(used)


def extend_plateau(manifest):
    prefix = "city-details-"
    archives = [name for name in manifest["archives"] if not name.startswith(prefix)]
    # The legacy composite veg-lod1 URL selected PlantCover, duplicating LOD3 grass as trees.
    base = [tile for tile in manifest["tiles"] if tile["archive"] < len(archives)
            and not (tile["kind"] == "tree" and "PlantCover" in tile["source"])]
    seen = defaultdict(set)
    needed = [tile for tile in base if tile["kind"] in ("road", "grass", "water", "tree")]
    with ThreadPoolExecutor(max_workers=12) as pool:
        for tile, data in zip(needed, pool.map(lambda t: get(t["source"]), needed)):
            seen[tile["kind"]].update(batch_table(data)["gml_id"])
    rows = json.loads(get(DATASETS))["datasets"]
    kinds = {"brid_lod2": "bridge", "brid_lod3": "bridge", "frn_lod3": "furniture",
             "veg_PlantCover_lod3": "grass", "veg_SolitaryVegetationObject_lod3": "tree",
             "wtr_lod1": "water", "tran_lod3": "road"}
    candidates = []
    catalogs = []
    trees = []
    # Detailed bridge LOD3 has priority wherever the two LODs cover the same official feature.
    relevant = [row for row in rows if row["year"] == YEAR and row["format"] == "3D Tiles"
                and row.get("city_code") in ("13101", "13102", "13103")
                and row["id"].partition("_")[2] in kinds]
    for row in sorted(relevant, key=lambda d: (-int(d["lod"]), d["id"])):
        ward, _, spec = row["id"].partition("_")
        kind = kinds.get(spec)
        if kind == "road" and ward != "13103":
            continue
        if ward == "13101" and kind in ("water", "grass"):
            continue
        views = FURNITURE_VIEWS if kind == "furniture" else CITY_VIEW
        urls = list(detail_urls(json.loads(get(row["url"]))["root"], row["url"], views))
        if urls:
            catalogs.append(row["url"])
        candidates.extend((row, kind, views, url) for url in urls)

    def additions():
        with ThreadPoolExecutor(max_workers=10) as pool:
            for (row, kind, views, url), raw in zip(candidates, pool.map(lambda c: get(c[3]), candidates)):
                table = batch_table(raw)
                identifiers = table["gml_id"]
                if all(feature in seen[kind] for feature in identifiers):
                    continue
                record, glb = unpack((row["id"], "grass-detail" if kind == "grass" else kind, url))
                glb, kept = clip_detail(glb, record["center"], table, seen[kind], views,
                                        trees if kind == "tree" else None)
                if not kept:
                    continue
                seen[kind].update(identifiers[i] for i in kept)
                record.update(kind=kind, lod=int(row["lod"]), featureCount=len(kept))
                if kind == "bridge":
                    record["functions"] = dict(Counter(table["brid:function"][i] for i in kept))
                yield record, glb

    records, extra = pack_archives(additions(), prefix=prefix.rstrip("-"), start=len(archives))
    manifest.update(tiles=base + records, archives=archives + extra, surveyedTrees=trees)
    manifest["catalogs"] = list(dict.fromkeys([c for c in manifest["catalogs"] if "veg-lod1-" not in c] + catalogs))
    print("Additional tiles:", Counter(tile["kind"] for tile in records), flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extend-plateau", action="store_true", help="refresh detailed layers while retaining base archives")
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(exist_ok=True)
    if args.extend_plateau:
        manifest = json.loads((ASSETS / "manifest.json").read_text())
        extend_plateau(manifest)
        # Imagery and elevation can be refreshed independently while geometry is being prepared.
        current = json.loads((ASSETS / "manifest.json").read_text())
        current.update({key: manifest[key] for key in ("tiles", "archives", "catalogs", "surveyedTrees")})
        write_manifest(current)
        return
    with ThreadPoolExecutor(max_workers=8) as pool:
        catalogs_and_tiles = list(pool.map(tiles, LAYERS.items()))
    entries = [entry for catalog, items in catalogs_and_tiles for entry in items]
    print("Selected", len(entries), "PLATEAU tiles", flush=True)
    with ThreadPoolExecutor(max_workers=12) as pool:
        records, archives = pack_archives(pool.map(unpack, entries))
    photo = image_tiles("seamlessphoto", 17, 2780, "jpg", "ground.jpg")
    elevation = image_tiles("dem_png", 14, 2780, "png", "elevation.png")
    manifest = {"origin": ORIGIN, "year": YEAR, "tiles": records, "archives": archives,
                "photo": photo, "elevation": elevation,
                "catalogs": [catalog for catalog, items in catalogs_and_tiles]}
    extend_plateau(manifest)
    write_manifest(manifest)


def write_manifest(manifest):
    staging = ASSETS / "manifest.json.new"
    staging.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    staging.replace(ASSETS / "manifest.json")

if __name__ == "__main__":
    main()
