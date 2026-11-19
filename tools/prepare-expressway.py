# /// script
# requires-python = ">=3.11"
# dependencies = ["DracoPy==2.2.0", "numpy==2.3.5", "opencv-python-headless==4.11.0.86"]
# ///
"""Project the PLATEAU carriageway and bridge-deck outlines into an asphalt colour map."""

import gzip
import json
import struct
from pathlib import Path

import cv2
import DracoPy
import numpy as np

TOOLS = Path(__file__).resolve().parent
ASSETS = TOOLS.parent / "assets"
SETTINGS = json.loads((TOOLS / "nihonbashi-expressway.json").read_text())


def primitives(glb):
    end = 20 + struct.unpack_from("<I", glb, 12)[0]
    doc = json.loads(glb[20:end])
    body = memoryview(glb)[end + 8 :]

    def view(index):
        desc = doc["bufferViews"][index]
        start = desc.get("byteOffset", 0)
        return body[start : start + desc["byteLength"]]

    def accessor(index):
        desc = doc["accessors"][index]
        dtype = {5121: "u1", 5123: "<u2", 5125: "<u4", 5126: "<f4"}[
            desc["componentType"]
        ]
        return np.frombuffer(view(desc["bufferView"]), dtype=dtype)

    for mesh in doc["meshes"]:
        for prim in mesh["primitives"]:
            draco = prim.get("extensions", {}).get("KHR_draco_mesh_compression")
            if draco:
                decoded = DracoPy.decode(bytes(view(draco["bufferView"])))
                ids = decoded.get_attribute_by_unique_id(
                    draco["attributes"]["_BATCHID"]
                )
                yield decoded.points, ids["data"].ravel(), decoded.faces
                continue
            attrs = prim["attributes"]
            yield (
                accessor(attrs["POSITION"]).reshape(-1, 3),
                accessor(attrs["_BATCHID"]),
                accessor(prim["indices"]).reshape(-1, 3),
            )


def build():
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    west, south, east, north = SETTINGS["bounds"]
    # Twice delivery resolution keeps 18–22 cm painted edges visible without jagged corners.
    scale = SETTINGS["pixelsPerMeter"] * 2
    mask = np.zeros(((north - south) * scale, (east - west) * scale), np.uint8)
    lon, lat, altitude = manifest["origin"]
    lon, lat = np.radians([lon, lat])
    up = np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    radius = 6378137 / np.sqrt(1 - 0.00669437999014 * np.sin(lat) ** 2)
    origin = (radius + altitude) * up
    origin[2] -= radius * 0.00669437999014 * np.sin(lat)
    east_axis = [-np.sin(lon), np.cos(lon), 0]
    basis = np.column_stack((east_axis, np.cross(up, east_axis), up))
    for index, filename in enumerate(manifest["archives"]):
        tiles = [
            t
            for t in manifest["tiles"]
            if t["id"] in SETTINGS["tiles"] and t["archive"] == index
        ]
        if not tiles:
            continue
        archive = gzip.decompress((ASSETS / filename).read_bytes())
        for t in tiles:
            for pos, ids, faces in primitives(
                archive[t["offset"] : t["offset"] + t["length"]]
            ):
                ecef = pos[:, [0, 2, 1]].astype(float) * [1, -1, 1] + (
                    t["center"] - origin
                )
                world = ecef @ basis
                xy = world[:, :2]
                points = world[faces]
                normal = np.cross(
                    points[:, 1] - points[:, 0], points[:, 2] - points[:, 0]
                )
                sky = (
                    normal[:, 2]
                    > np.linalg.norm(normal, axis=1) * SETTINGS["minimumUpNormal"]
                )
                valid = sky & np.isin(ids[faces[:, 0]], SETTINGS["tiles"][t["id"]])
                if t["kind"] == "bridge":
                    valid &= points[:, :, 2].min(axis=1) >= SETTINGS["bridgeDeckHeight"]
                pixels = np.rint((xy - [west, north]) * [scale, -scale]).astype(
                    np.int32
                )
                for tri in faces[valid]:
                    cv2.fillConvexPoly(mask, pixels[tri], 255)
    # Closing subpixel tile joins keeps transverse markings out of continuous pavement.
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    distance = cv2.distanceTransform(mask, cv2.DIST_L2, 5) / scale
    edge = np.maximum(0, 1 - np.maximum(0, np.abs(distance - 0.59) - 0.074) / 0.13)
    # The deck atlas includes transverse cut edges at tile ends and beside the ramps.
    # Mark the long margins of this east–west reach only.
    dy = cv2.Sobel(distance, cv2.CV_32F, 0, 1, ksize=3)
    dx = cv2.Sobel(distance, cv2.CV_32F, 1, 0, ksize=3)
    edge *= np.clip((abs(dy) / (abs(dx) + abs(dy) + 1e-5) - 0.25) * 2.5, 0, 1)
    shoulder = np.exp(-(((distance - 0.25) / 0.22) ** 2))
    # The farther bounds are for texture sampling only. No edge should smear beyond the frame.
    fade = np.ones(mask.shape[1], np.float32)
    fade[: 2 * scale] = np.linspace(0, 1, 2 * scale)
    fade[-2 * scale :] = fade[: 2 * scale][::-1]
    tone = 93 + (92 * edge - 9 * shoulder) * fade
    tone[: 2 * scale] = 93 + (tone[: 2 * scale] - 93) * fade[: 2 * scale, None]
    tone[-2 * scale :] = 93 + (tone[-2 * scale :] - 93) * fade[-2 * scale :, None]
    tone = cv2.resize(tone, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    rng = np.random.default_rng(314159)
    grain = rng.normal(0, 1.8, tone.shape)
    wear = cv2.resize(
        rng.normal(0, 1.4, (tone.shape[0] // 12, tone.shape[1] // 12)),
        (tone.shape[1], tone.shape[0]),
        interpolation=cv2.INTER_CUBIC,
    )
    # Keep plain padding constant so it can safely be sampled with ClampToEdge.
    result = np.rint(
        tone
        + (grain + wear)
        * cv2.resize(mask, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        / 255
    )
    result[[0, -1], :] = 93
    result[:, [0, -1]] = 93
    cv2.imwrite(
        str(ASSETS / SETTINGS["file"]),
        result.astype(np.uint8),
        [cv2.IMWRITE_PNG_COMPRESSION, 9],
    )


if __name__ == "__main__":
    build()
