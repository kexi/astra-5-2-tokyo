# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.3.5", "opencv-python-headless==4.11.0.86"]
# ///
"""Protect plain walls, curtain-wall grids and repeated braces in the measured elevations."""

import argparse
import gzip
import json
import multiprocessing as mp
from pathlib import Path

import cv2
import numpy as np
from blender_texture_screen import walls


def measures(wall):
    if wall is None:
        return None
    h, w = wall.shape[:2]
    y = np.minimum((np.arange(8) + 0.5) * h / 8, h - 1).astype(int)
    x = np.minimum((np.arange(8) + 0.5) * w / 8, w - 1).astype(int)
    sample = np.ix_(y, x)
    valid = (wall.max(2) > 0).astype("f4")
    kernel = (max(7, min(29, round(w * 0.26))), max(7, min(29, round(h * 0.26))))

    def pool(values):
        return cv2.boxFilter(values, -1, kernel, normalize=True)[sample]

    g = cv2.cvtColor(wall, cv2.COLOR_BGR2GRAY).astype("f4")
    # Exclude building silhouettes; a diagonal roof outline against empty
    # screen space is not evidence about the texture on a wall below it.
    interior = cv2.erode(valid, np.ones((5, 5), "u1"))
    dx = cv2.Sobel(g, -1, 1, 0, ksize=3) * 0.125
    dy = cv2.Sobel(g, -1, 0, 1, ksize=3) * 0.125
    strength = np.hypot(dx, dy) * interior
    angle = np.mod(np.arctan2(dy, dx), np.pi)
    axial = abs(np.cos(2 * angle)) > 0.82
    votes = np.stack(
        [
            pool(strength * (abs(np.cos(angle - a)) > np.cos(np.pi / 9)))
            for a in np.arange(8) * np.pi / 8
        ]
    )
    mag = np.maximum(0.001, pool(strength))
    diagonal = 1 - pool(strength * axial) / mag
    directional = votes.max(0) / mag
    # Oblique structure repeated at a fixed displacement should be preserved
    # even where it looks like a rooftop perimeter in a single small patch.
    periodic = np.zeros((8, 8), "f4")
    for axis in (0, 1):
        step_dim = (h, w)[axis]
        for step in (4, 7, 11, 16):
            if step_dim < 3 * step:
                continue
            obs = np.roll(g, step, axis)
            usable = interior * np.roll(interior, step, axis)
            if axis == 0:
                usable[:step] = 0
            else:
                usable[:, :step] = 0
            v = np.maximum(0.001, pool(usable))
            a, b = pool(g * usable) / v, pool(obs * usable) / v
            var_a = np.maximum(0, pool(g * g * usable) / v - a * a)
            var_b = np.maximum(0, pool(obs * obs * usable) / v - b * b)
            corr = (pool(g * obs * usable) / v - a * b) / np.sqrt(
                np.maximum(1, var_a * var_b)
            )
            reliable = (v > 0.78) & (var_a > 60) & (var_b > 60)
            periodic = np.maximum(periodic, np.where(reliable, corr, 0))
    result = np.stack(
        [mag, diagonal * 100, directional * 100, periodic * 100, pool(interior) * 100],
        axis=2,
    )
    return np.rint(result).clip(0, 255).astype("u1").reshape(-1, 5).tolist()


def entry(path):
    return path.stem, [measures(w) for w in walls(path)]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("elevations", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()
    files = sorted(args.elevations.glob("*/*.png"))
    with (
        gzip.open(args.output, "wt") as stream,
        mp.get_context("spawn").Pool(args.workers) as pool,
    ):
        stream.write("{")
        for k, (identifier, sides) in enumerate(
            pool.imap_unordered(entry, files, chunksize=16)
        ):
            stream.write(
                ("," if k else "")
                + json.dumps(identifier)
                + ":"
                + json.dumps(sides, separators=(",", ":"))
            )
        stream.write("}")


if __name__ == "__main__":
    cv2.setNumThreads(1)
    main()
