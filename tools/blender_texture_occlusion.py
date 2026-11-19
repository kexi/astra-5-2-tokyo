# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.3.5", "opencv-python-headless==4.11.0.86"]
# ///
"""Trace possible aerial views of photographic wall pixels against the city's measured roofs."""

import argparse
import gzip
import json
from pathlib import Path

import cv2
import numpy as np

GRID = 8
SPACING = 0.70


def height_model(roofs, owners):
    low = roofs[:, :, :2].min(axis=(0, 1)) - 2
    xy = np.rint((roofs[:, :, :2] - low) / SPACING).astype("i4")
    shape = tuple(xy.max(axis=(0, 1))[::-1] + 2)
    height = np.full(shape, -1000, "f4")
    owner = np.full(shape, -1, "i4")
    # A sloped roof is given the height of its lowest corner. This deliberately
    # understates blockage and avoids falsely assigning a roof to the facade below.
    z = roofs[:, :, 2].min(1)
    for j in np.argsort(z):
        cv2.fillConvexPoly(height, xy[j], float(z[j]))
        cv2.fillConvexPoly(owner, xy[j], int(owners[j]))
    return height, owner, low


def obstruction(points, front, buildings, height, owner, low):
    valid = np.isfinite(points[:, :, 2])
    points = np.nan_to_num(points)
    maximum = np.full(valid.shape, np.inf)
    for yaw in np.radians([-22, 0, 22]):
        rot = np.array([[np.cos(yaw), np.sin(yaw)], [-np.sin(yaw), np.cos(yaw)]])
        direction = (front @ rot)[:, None, :]
        angle = np.zeros(valid.shape, "f4")
        for length in (2, 3.5, 5, 7, 10, 13, 17, 22, 28, 36, 46, 60, 78, 100):
            ix = np.rint(
                (points[:, :, :2] + length * direction - low) / SPACING
            ).astype("i4")
            x = np.clip(ix[:, :, 0], 0, owner.shape[1] - 1)
            y = np.clip(ix[:, :, 1], 0, owner.shape[0] - 1)
            other = (owner[y, x] != buildings[:, None]) & (owner[y, x] >= 0)
            slope = (height[y, x] - 1.5 - points[:, :, 2]) / length
            np.maximum(angle, slope, out=angle, where=other)
        maximum = np.minimum(maximum, angle)
    scores = np.degrees(np.arctan(maximum)).round().astype("i1")
    scores[~valid] = -1
    return scores


def run(source, output):
    data = np.load(source)
    ids = data["ids"]
    height, owner, low = height_model(data["roof"], data["owners"])
    points, front, indices = data["points"], data["directions"], data["indices"]
    result = {}
    for begin in range(0, len(front), 256):
        group = slice(begin, begin + 256)
        horizons = obstruction(
            points[group], front[group], indices[group, 0], height, owner, low
        )
        for (building, side), values in zip(indices[group], horizons, strict=True):
            if values.max() >= 25:
                result.setdefault(str(ids[building]), {})[str(side)] = values.tolist()
    with gzip.open(output, "wt") as stream:
        json.dump(result, stream, separators=(",", ":"))
    print(
        json.dumps(
            {
                "buildings": len(ids),
                "measured_elevations": len(front),
                "potentially_occluded": len(result),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run(args.source, args.output)
