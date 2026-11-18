# /// script
# requires-python = ">=3.11"
# ///
"""Cut official PLATEAU and GSI layers around Tokyo Station."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import urlopen
import gzip
import hashlib
import json
import math
import struct
import subprocess
import tempfile
import time

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
    "trees": ("13101", "veg-lod1", "tree"),
}
YEAR = 2025

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
    data = resize_textures(gltf, data[json_end+8:], center, kind)
    return {"id": f"{name}-{Path(url).stem}", "kind": kind, "center": center, "source": url}, data

def resize_textures(gltf, binary, center, kind):
    lon, lat = map(math.radians, ORIGIN[:2])
    n = 6378137 / math.sqrt(1 - .00669438 * math.sin(lat)**2)
    base = (n*math.cos(lat)*math.cos(lon), n*math.cos(lat)*math.sin(lon), n*.99330562*math.sin(lat))
    distance = math.dist(center, base)
    width = 1536 if distance < 490 else 896 if distance < 1080 else 512 if distance < 1900 else 320
    image_views = {image["bufferView"]: image["mimeType"] for image in gltf.get("images", [])}
    body = bytearray()
    for i, view in enumerate(gltf["bufferViews"]):
        start = view.get("byteOffset", 0)
        chunk = binary[start:start + view["byteLength"]]
        if kind == "building" and i in image_views and len(chunk) > 9000:
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

def pack_archives(entries):
    records, archives = [], []
    part = bytearray()

    def flush():
        filename = f"city-{len(archives):02d}.bin.gz"
        target = ASSETS / filename
        staging = target.with_suffix(".new")
        staging.write_bytes(gzip.compress(part, compresslevel=4, mtime=0))
        staging.replace(target)
        archives.append(filename)
        part.clear()

    for index, (record, glb) in enumerate(entries, 1):
        if part and len(part) + len(glb) > ARCHIVE_LIMIT:
            flush()
        record.update(archive=len(archives), offset=len(part), length=len(glb))
        part.extend(glb)
        records.append(record)
        if index % 60 == 0:
            print(f"Packed {index} tiles", flush=True)
    if part:
        flush()
    return records, archives

def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(exist_ok=True)
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
    (ASSETS / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

if __name__ == "__main__":
    main()
