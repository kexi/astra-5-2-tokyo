# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Export four measured photographic elevations from the original editable model."""

import ctypes as c
import json
import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_building_textures import geometry, links, pixels
from blender_buildings import read_values
from blender_texture_original import tile_photo

HERE = Path(__file__).resolve().parent
SIDE = 160


def library(directory):
    review = runpy.run_path(str(HERE / "review-building-textures.py"))
    target = Path(directory) / "elevations.dylib"
    subprocess.run(
        [
            os.getenv("CXX", "c++"),
            "-std=c++17",
            "-O3",
            "-shared",
            "-fPIC",
            str(HERE / "review-building-facade.cpp"),
            "-lz",
            "-o",
            str(target),
        ],
        check=True,
    )
    native = c.CDLL(str(target))
    paint = review["Paint"]
    native.render_facade.argtypes = [
        c.c_int,
        c.c_void_p,
        c.c_void_p,
        c.c_void_p,
        c.POINTER(paint),
        c.c_int,
        c.c_void_p,
        c.c_void_p,
    ]
    native.save_review_png.argtypes = [c.c_char_p, c.c_int, c.c_int, c.c_void_p]
    return native, paint


def elevations(obj, source, cache, native, paint):
    mesh = obj.data
    xyz, _, normal, area = geometry(obj)
    indices = read_values(mesh.polygons, "material_index", np.int32).ravel()
    src = read_values(mesh.uv_layers.active.uv, "vector", np.float32, 2).reshape(
        -1, 3, 2
    )
    maps = {}
    for slot in np.unique(indices):
        photo = links(mesh.materials[slot])
        if photo is not None:
            maps[slot] = (
                source
                if source is not None and "repairedFacadeTriangles" not in obj
                else pixels(photo, cache)
            )
    outputs = np.zeros((SIDE, SIDE * 4, 3), np.uint8)
    if not maps:
        return outputs
    occupied = np.isin(indices, list(maps))
    lookup = np.zeros(len(mesh.materials), np.int32)
    paints = (paint * len(maps))()
    for j, (slot, rgb) in enumerate(maps.items()):
        lookup[slot] = j
        paints[j] = paint(rgb.ctypes.data, rgb.shape[1], rgb.shape[0], 0, (0, 0, 0))
    axis = np.angle(np.sum(area * (normal[:, 0] + 1j * normal[:, 1]) ** 4)) / 4
    for side in range(4):
        angle = axis + np.pi / 2 * (1 - side)
        front = np.array([np.cos(angle), np.sin(angle), 0])
        right = np.array([-np.sin(angle), np.cos(angle), 0])
        take = occupied & (normal @ front > 0.94) & (abs(normal[:, 2]) < 0.35)
        if not take.any():
            continue
        p = xyz[take]
        metric = np.ascontiguousarray(
            np.stack((-(p @ right), p @ front, p[:, :, 2]), axis=2), np.float32
        )
        uv = np.ascontiguousarray(src[take])
        slots = np.ascontiguousarray(lookup[indices[take]])
        out = np.empty((SIDE, SIDE, 3), np.uint8)
        owner = np.empty((SIDE, SIDE), np.uint32)
        native.render_facade(
            len(p),
            metric.ctypes.data,
            uv.ctypes.data,
            slots.ctypes.data,
            paints,
            SIDE,
            out.ctypes.data,
            owner.ctypes.data,
        )
        out[owner == 0xFFFFFFFF] = 0
        outputs[:, side * SIDE : (side + 1) * SIDE] = out
    return outputs


def export_elevations(target, tiles=None):
    output = Path(target)
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((HERE.parent / "assets/manifest.json").read_text())
    centres = {}
    with tempfile.TemporaryDirectory(prefix="facade-screen-") as temp:
        native, paint = library(temp)
        for item in manifest["tiles"]:
            tile = item["id"]
            if item["kind"] != "building" or (tiles and tile not in tiles):
                continue
            folder = output / tile
            folder.mkdir(exist_ok=True)
            group = bpy.data.collections[tile]
            for obj in group.objects:
                centres[obj["id"]] = list(obj.matrix_world.translation[:2])
            remaining = [
                o
                for o in group.objects
                if o["imageFaces"] and not (folder / (o["id"] + ".png")).exists()
            ]
            if remaining:
                source = tile_photo(tile)
                cache = {}
                for obj in remaining:
                    if obj.get("textureVersion"):
                        raise ValueError(
                            "Use the original model for photographic review"
                        )
                    picture = elevations(obj, source, cache, native, paint)
                    dest = folder / (obj["id"] + ".png")
                    temporary = dest.with_suffix(".tmp")
                    if not native.save_review_png(
                        os.fsencode(temporary),
                        picture.shape[1],
                        picture.shape[0],
                        picture.ctypes.data,
                    ):
                        raise OSError(f"Cannot write {dest}")
                    temporary.replace(dest)
            print(json.dumps({"tile": tile, "rendered": len(remaining)}), flush=True)
    (output / "centres.json").write_text(json.dumps(centres, separators=(",", ":")))


if __name__ == "__main__":
    export_elevations(sys.argv[sys.argv.index("--") + 1])
