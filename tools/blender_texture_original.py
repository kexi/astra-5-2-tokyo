"""Read the original PLATEAU photo at its native resolution for a building tile."""

import hashlib
import json
import struct
import tempfile
from functools import cache
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import OpenImageIO as oiio
from facade_atlas import FACADES

ROOT = Path(__file__).resolve().parents[1]


@cache
def sources():
    manifest = json.loads((ROOT / "assets/manifest.json").read_text())
    return {t["id"]: t["source"] for t in manifest["tiles"] if t["kind"] == "building"}


def source_data(url):
    cache_dir = Path(tempfile.gettempdir()) / "tokyo-flight-data/http"
    path = cache_dir / hashlib.sha256(url.encode()).hexdigest()
    if path.is_file():
        return path.read_bytes()
    cache_dir.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=60) as response:
        data = response.read()
    if not data.startswith(b"b3dm"):
        raise ValueError(f"Expected a PLATEAU b3dm at {url}")
    with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False) as tmp:
        tmp.write(data)
    Path(tmp.name).replace(path)
    return data


def tile_photo(tile_id):
    # These three city tiles contain reviewed, pixel-level facade repairs.
    if tile_id in FACADES:
        return None
    raw = source_data(sources()[tile_id])
    magic, version, size, *tables = struct.unpack_from("<4s6I", raw)
    if (magic, version, size) != (b"b3dm", 1, len(raw)):
        raise ValueError(f"Invalid b3dm header: {tile_id}")
    glb = 28 + sum(tables)
    header = struct.unpack_from("<I", raw, glb + 12)[0]
    doc = json.loads(raw[glb + 20 : glb + 20 + header])
    images = doc.get("images", [])
    if not images:
        return None
    if len(images) != 1 or images[0]["mimeType"] != "image/webp":
        raise ValueError(f"Expected one source photo atlas per tile: {tile_id}")
    view = doc["bufferViews"][images[0]["bufferView"]]
    start = glb + 28 + header + view.get("byteOffset", 0)
    with tempfile.NamedTemporaryFile(suffix=".webp") as image_file:
        image_file.write(raw[start : start + view["byteLength"]])
        image_file.flush()
        reader = oiio.ImageInput.open(image_file.name)
        if reader is None:
            raise OSError(f"Cannot decode the original WebP for {tile_id}")
        try:
            return np.ascontiguousarray(reader.read_image(oiio.UINT8)[::-1, :, :3])
        finally:
            reader.close()
