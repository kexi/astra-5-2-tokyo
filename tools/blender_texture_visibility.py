# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Export each wall's measured world points for the neighbouring-roof visibility test."""

import sys
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_building_textures import geometry, links
from blender_buildings import read_values

GRID = 8


def wall_samples(tri, right, front):
    coordinates = np.stack((tri @ right, -tri[:, :, 2]), axis=2)
    corners = coordinates.reshape(-1, 2)
    lower = corners.min(0)
    extent = np.ptp(corners, axis=0)
    yy, xx = np.indices((GRID, GRID))
    sample = lower + (np.c_[xx.ravel(), yy.ravel()] + 0.5) / GRID * extent
    distance = np.full(len(sample), -np.inf)
    for vertices, projected in zip(tri, coordinates, strict=True):
        basis = projected[1:] - projected[0]
        if abs(np.linalg.det(basis)) < 0.00001:
            continue
        weights = (sample - projected[0]) @ np.linalg.inv(basis)
        inside = (weights.min(1) >= 0) & (weights.sum(1) <= 1)
        depth = vertices[0] @ front + weights @ ((vertices[1:] - vertices[0]) @ front)
        np.maximum(distance, depth, where=inside, out=distance)
    valid = np.isfinite(distance)
    world = np.full((len(sample), 3), np.nan, dtype="f4")
    world[valid] = sample[valid, :1] * right + distance[valid, None] * front
    world[valid, 2] = -sample[valid, 1]
    return world


def export(output):
    identifiers, roofs, owners, points, directions, indices = [], [], [], [], [], []
    for obj in bpy.data.objects:
        if not obj.get("id") or "imageFaces" not in obj:
            continue
        identifier = obj["id"]
        index = len(identifiers)
        identifiers.append(identifier)
        xyz, _, normal, area = geometry(obj)
        roof = xyz[normal[:, 2] > 0.45].astype("f4")
        roofs.append(roof)
        owners.append(np.full(len(roof), index, dtype="i4"))
        if not obj.get("imageFaces"):
            continue
        slots = read_values(obj.data.polygons, "material_index", np.int32).ravel()
        photo = np.isin(
            slots, [i for i, m in enumerate(obj.data.materials) if links(m)]
        )
        axis = np.angle(np.sum(area * (normal[:, 0] + 1j * normal[:, 1]) ** 4)) / 4
        for side in range(4):
            angle = axis + np.pi / 2 * (1 - side)
            front = np.array([np.cos(angle), np.sin(angle), 0])
            right = np.array([-np.sin(angle), np.cos(angle), 0])
            take = photo & (normal @ front > 0.94) & (abs(normal[:, 2]) < 0.35)
            if take.any():
                points.append(wall_samples(xyz[take], right, front))
                directions.append(front[:2])
                indices.append((index, side))
    np.savez_compressed(
        output,
        ids=identifiers,
        roof=np.concatenate(roofs),
        owners=np.concatenate(owners),
        points=np.asarray(points),
        directions=np.asarray(directions, "f4"),
        indices=np.asarray(indices, "i4"),
    )
    print(
        f"Exported {len(identifiers)} measured buildings and {len(points)} elevations",
        flush=True,
    )


if __name__ == "__main__":
    export(sys.argv[sys.argv.index("--") + 1])
