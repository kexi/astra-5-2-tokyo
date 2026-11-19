"""Sample georeferenced roof imagery in the Blender scene's metric ENU frame."""

import json
from functools import cache
from pathlib import Path

import numpy as np
import OpenImageIO as oiio

ASSETS = Path(__file__).resolve().parents[1] / "assets"


@cache
def source_layers():
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    survey = json.loads((ASSETS / "survey.json").read_text())
    edges = json.loads((ASSETS / "roof-orthophotos/layers.json").read_text())
    return manifest["origin"], [*survey["photos"], manifest["photo"], *edges]


@cache
def srgb_image(filename):
    image = oiio.ImageInput.open(str(ASSETS / filename))
    if image is None:
        raise OSError(f"Cannot read the roof orthophoto {filename}")
    try:
        return np.ascontiguousarray(image.read_image(oiio.UINT8)[:, :, :3])
    finally:
        image.close()


def mercator(east, north, up=0.0):
    """Transform local east/north/up into XYZ tile coordinates at zoom zero."""
    origin, _ = source_layers()
    lon, lat = np.radians(origin[:2])
    sl, cl, sp, cp = np.sin(lon), np.cos(lon), np.sin(lat), np.cos(lat)
    a = 6378137.0
    e2 = 0.0066943799901413165
    b = a * np.sqrt(1 - e2)
    radius = a / np.sqrt(1 - e2 * sp**2)
    east, north = np.broadcast_arrays(east, north)
    axial = (radius + origin[2] + up) * cp - north * sp
    x = axial * cl - east * sl
    y = axial * sl + east * cl
    z = (radius * (1 - e2) + origin[2] + up) * sp + north * cp
    p = np.hypot(x, y)
    beta = np.arctan2(z * a, p * b)
    latitude = np.arctan2(
        z + e2 / (1 - e2) * b * np.sin(beta) ** 3,
        p - e2 * a * np.cos(beta) ** 3,
    )
    return (
        0.5 + np.arctan2(y, x) / (2 * np.pi),
        0.5 - np.arcsinh(np.tan(latitude)) / (2 * np.pi),
    )


def sample_roof(east, north, up=0.0):
    """Return sRGB bytes and a coverage mask. Never clamp out-of-coverage roofs onto a nearby building."""
    x, y = mercator(east, north, up)
    _, layers = source_layers()
    rgb = np.zeros((x.size, 3), dtype=np.uint8)
    valid = np.zeros(x.size, dtype=bool)
    for layer in layers:
        if valid.all():
            break
        left, top, right, bottom = layer["bounds"]
        scale = 2 ** layer["zoom"]
        u = (x.ravel() * scale - left) / (right - left)
        v = (y.ravel() * scale - top) / (bottom - top)
        covered = ~valid & (u >= 0) & (u < 1) & (v >= 0) & (v < 1)
        if not np.any(covered):
            continue
        pixels = srgb_image(layer["file"])
        h, w = pixels.shape[:2]
        px = np.clip(u[covered] * w - 0.5, 0, w - 1)
        py = np.clip(v[covered] * h - 0.5, 0, h - 1)
        i, j = px.astype(int), py.astype(int)
        ni, nj = np.minimum(i + 1, w - 1), np.minimum(j + 1, h - 1)
        fx, fy = (px - i)[:, None], (py - j)[:, None]
        upper = pixels[j, i] * (1 - fx) + pixels[j, ni] * fx
        lower = pixels[nj, i] * (1 - fx) + pixels[nj, ni] * fx
        rgb[covered] = np.rint(upper * (1 - fy) + lower * fy).astype(np.uint8)
        valid[covered] = True
    return rgb.reshape((*x.shape, 3)), valid.reshape(x.shape)
