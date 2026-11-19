"""Rectify the two PLATEAU walls and recover room-to-room brightness for new glass panes."""

import gzip
import json
import struct
import tempfile
import zlib
from pathlib import Path

import bpy
import numpy as np


def save_srgb(path, linear):
    srgb = np.where(
        linear < 0.0031308,
        12.92 * linear,
        1.055 * np.maximum(linear, 0) ** (1 / 2.4) - 0.055,
    )
    rgb = np.rint(np.clip(srgb, 0, 1) * 255).astype(np.uint8)

    def chunk(tag, payload):
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload))
        )

    h, w = rgb.shape[:2]
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(b"\0" + r.tobytes() for r in rgb), 9))
        + chunk(b"IEND", b"")
    )


def recover(root, work, walls):
    manifest = json.loads((root / "assets/manifest.json").read_text())
    tile = next(t for t in manifest["tiles"] if t["id"] == "chiyoda-data454")
    source = gzip.decompress(
        (root / "assets" / manifest["archives"][tile["archive"]]).read_bytes()
    )
    with tempfile.TemporaryDirectory() as path:
        file = Path(path) / "wall.glb"
        file.write_bytes(source[tile["offset"] : tile["offset"] + tile["length"]])
        bpy.ops.import_scene.gltf(
            filepath=str(file),
            import_webp_texture=True,
            import_scene_as_collection=False,
            loglevel=4,
        )
    # Root/tile matrices are intentionally obtained from the same ENU placement as the city.
    import runpy

    place, _ = runpy.run_path(str(root / "tools/create-blender.py"))["geo"](manifest)
    tile_matrix = place(tile["center"])
    photos = {}
    wall_faces = {}
    for obj in tuple(bpy.context.scene.objects):
        if obj.type != "MESH" or "_BATCHID" not in obj.data.attributes:
            continue
        mesh = obj.data
        matrix = tile_matrix @ obj.matrix_world
        batch = mesh.attributes["_BATCHID"].data
        for f in mesh.polygons:
            if batch[f.vertices[0]].value != 1 or f.area < 280:
                continue
            pts = np.array([matrix @ mesh.vertices[i].co for i in f.vertices])
            if np.ptp(pts[:, 2]) < 25:
                continue
            for side, (a, b, _) in walls.items():
                a, b = np.array(a), np.array(b)
                direction = b - a
                length = np.linalg.norm(direction)
                direction /= length
                outward = np.array([-direction[1], direction[0]])
                plan = pts[:, :2] - a
                if np.max(abs(plan @ outward)) > 0.05:
                    continue
                image = next(
                    n.image
                    for n in mesh.materials[f.material_index].node_tree.nodes
                    if n.type == "TEX_IMAGE"
                )
                if image.name not in photos:
                    data = np.empty(image.size[1] * image.size[0] * 4, np.float32)
                    image.pixels.foreach_get(data)
                    photos[image.name] = data.reshape(image.size[1], image.size[0], 4)[
                        :, :, :3
                    ]
                uv = np.array(
                    [mesh.uv_layers.active.uv[i].vector for i in f.loop_indices]
                )
                st = np.column_stack([plan @ direction, pts[:, 2]])
                wall_faces.setdefault(side, []).append((st, uv, photos[image.name]))
    for side, (a, b, bays) in walls.items():
        length = np.linalg.norm(np.array(b) - a)
        width, height = round(length * 12), 264
        yy, xx = np.indices((height, width))
        x = (xx + 0.5) / width * length
        z = 31.76 - (yy + 0.5) / height * 22.08
        rect = np.zeros((height, width, 3), np.float32)
        faces = wall_faces[side]
        if len(faces) != 2:
            raise ValueError(
                f"Expected two exterior triangles on {side}; got {len(faces)}"
            )
        for st, uv, photo in faces:
            a, b, c = st
            e = b - a
            g = c - a
            det = e[0] * g[1] - e[1] * g[0]
            p = ((x - a[0]) * g[1] - (z - a[1]) * g[0]) / det
            q = (e[0] * (z - a[1]) - e[1] * (x - a[0])) / det
            k = (p >= 0) & (q >= 0) & (p + q <= 1)
            puv = uv[0] + p[k, None] * (uv[1] - uv[0]) + q[k, None] * (uv[2] - uv[0])
            rect[k] = photo[
                (puv[:, 1] * photo.shape[0]).astype(int).clip(0, photo.shape[0] - 1),
                (puv[:, 0] * photo.shape[1]).astype(int).clip(0, photo.shape[1] - 1),
            ]
        luminance = rect @ np.array([0.2126, 0.7152, 0.0722])
        pane_rows = np.clip(np.floor((z - 10.92) / 3.25).astype(int), 0, 6)
        pane_cols = np.clip(
            np.floor(x / length * bays * 3).astype(int), 0, bays * 3 - 1
        )
        room = pane_cols + bays * 3 * pane_rows
        # Estimate room tone only within the measured window strips; stone and frames
        # would otherwise push all the glass toward the same grey.
        phase = (z - 10.92) % 3.25
        glass = (phase > 0.17) & (phase < 1.95) & (luminance > 0.012)
        medians = np.array(
            [
                np.median(luminance[(room == i) & glass])
                if np.any((room == i) & glass)
                else 0.32
                for i in range(7 * bays * 3)
            ]
        )
        base = medians[room]
        lo, hi = np.percentile(medians, [12, 85])
        tone = np.clip((base - lo) / max(0.10, hi - lo), 0, 1)
        ratio = np.clip((luminance + 0.04) / (base + 0.04), 0.62, 1.42)
        dark = np.array([0.055, 0.094, 0.119])
        light = np.array([0.260, 0.279, 0.253])
        color = dark + (light - dark) * tone[:, :, None]
        color *= (0.74 + 0.26 * ratio + 0.14 * (np.clip(phase / 2.07, 0, 1) - 0.5))[
            :, :, None
        ]
        chroma = np.clip((rect + 0.04) / (luminance[:, :, None] + 0.04), 0.78, 1.20)
        color *= 0.54 + 0.46 * chroma
        save_srgb(work / f"glass-{side}.png", color)
    bpy.ops.wm.read_factory_settings(use_empty=True)
