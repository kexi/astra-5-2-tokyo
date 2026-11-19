# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.3.5", "opencv-python-headless==4.11.0.86"]
# ///
"""Confirm a wall's actual photographic features against the local aerial imagery."""

import argparse
import gzip
import json
import multiprocessing as mp
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

ASSETS = Path(__file__).resolve().parents[1] / "assets"
SPACING = 0.45
CELL = 100
SIDE = 572


@lru_cache(maxsize=1)
def layers():
    m = json.loads((ASSETS / "manifest.json").read_text())
    s = json.loads((ASSETS / "survey.json").read_text())
    extras = json.loads((ASSETS / "roof-orthophotos/layers.json").read_text())
    return m["origin"], [*s["photos"], m["photo"], *extras]


@lru_cache(maxsize=12)
def gray_image(name):
    image = cv2.imread(str(ASSETS / name), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"Unable to read {name}")
    return image


@lru_cache(maxsize=1)
def detector():
    return cv2.SIFT_create(1600, 3, 0.015, 12)


@lru_cache(maxsize=32)
def reference(cell_x, cell_y):
    origin, photos = layers()
    gx = (np.arange(SIDE) - SIDE / 2) * SPACING + cell_x * CELL
    gy = (SIDE / 2 - np.arange(SIDE)) * SPACING + cell_y * CELL
    lat = np.deg2rad(origin[1])
    meters = 40075016.69 * np.cos(lat)
    tx0 = origin[0] / 360 + 0.5
    ty0 = 0.5 - np.arcsinh(np.tan(lat)) / (2 * np.pi)
    # This tangent-plane approximation only locates a 257m search window.
    # The matched homography itself uses the real aerial pixels.
    tx = tx0 + gx / meters
    ty = ty0 - gy / meters
    picture = np.zeros((SIDE, SIDE), np.uint8)
    covered = np.zeros_like(picture, bool)
    for layer in photos:
        left, top, right, bottom = layer["bounds"]
        scale = 2 ** layer["zoom"]
        u = (tx * scale - left) / (right - left)
        v = (ty * scale - top) / (bottom - top)
        valid = (v[:, None] >= 0) & (v[:, None] < 1) & (u >= 0) & (u < 1) & ~covered
        if not valid.any():
            continue
        source = gray_image(layer["file"])
        mx, my = np.meshgrid(
            (u * source.shape[1] - 0.5).astype("f4"),
            (v * source.shape[0] - 0.5).astype("f4"),
        )
        values = cv2.remap(
            source, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
        )
        picture[valid] = values[valid]
        covered |= valid
        if covered.all():
            break
    kp, desc = detector().detectAndCompute(picture, covered.astype("u1") * 255)
    if desc is None:
        return None
    matcher = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 4}, {"checks": 40})
    matcher.add([desc])
    matcher.train()
    return picture, np.array([k.pt for k in kp], dtype="f4"), matcher


def matches(panel, centre):
    if panel is None or min(panel.shape[:2]) < 16:
        return []
    result = reference(*(round(float(c) / CELL) for c in centre[:2]))
    if result is None:
        return []
    aerial, target, matcher = result
    gray = cv2.cvtColor(panel, cv2.COLOR_BGR2GRAY)
    valid = cv2.erode((np.max(panel, axis=2) > 0).astype("u1"), np.ones((3, 3), "u1"))
    found = []
    for stretch in (1.0, 0.60, 1.65):
        height = max(12, round(gray.shape[0] * stretch))
        ratio = height / gray.shape[0]
        canvas = cv2.resize(gray, (gray.shape[1], height))
        active = cv2.resize(valid, canvas.shape[::-1], interpolation=cv2.INTER_NEAREST)
        kp, desc = detector().detectAndCompute(canvas, active)
        if desc is None or len(desc) < 6:
            continue
        pairs = matcher.knnMatch(desc, k=2)
        good = [
            m[0] for m in pairs if len(m) == 2 and m[0].distance < 0.74 * m[1].distance
        ]
        if len(good) < 7:
            continue
        # SIFT can emit several orientations at one detector location.
        unique = {}
        for m in sorted(good, key=lambda v: v.distance):
            pt = kp[m.queryIdx].pt
            key = (round(pt[0]), round(pt[1]))
            if key not in unique:
                unique[key] = m
        good = list(unique.values())
        if len(good) < 7:
            continue
        a = np.float32([kp[q.queryIdx].pt for q in good])
        a[:, 1] /= ratio
        b = target[[q.trainIdx for q in good]]
        H, inliers = cv2.findHomography(a, b, cv2.RANSAC, 3.2, maxIters=1500)
        if H is None:
            continue
        chosen = inliers.ravel().astype(bool)
        if chosen.sum() < 7 or chosen.mean() < 0.45:
            continue
        hull = cv2.convexHull(a[chosen]).reshape(-1, 2)
        if (
            cv2.contourArea(hull) < 95
            or cv2.contourArea(cv2.convexHull(b[chosen])) < 90
        ):
            continue
        projected = cv2.warpPerspective(
            aerial, H, gray.shape[::-1], flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
        )
        mask = np.zeros(gray.shape, "u1")
        cv2.fillConvexPoly(mask, np.rint(hull).astype("i4"), 1)
        pick = (mask & valid).astype(bool)
        sig = gray[pick].astype("f4")
        ref = projected[pick].astype("f4")
        if len(sig) < 75 or min(sig.std(), ref.std()) < 8:
            continue
        corr = float(np.corrcoef(sig, ref)[0, 1])
        if corr < 0.43:
            continue
        hull /= np.array(gray.shape[::-1])
        found.append(
            {
                "points": int(chosen.sum()),
                "correlation": round(corr, 3),
                "contour": np.round(hull, 4).tolist(),
            }
        )
        break
    return found


def confirm(entry):
    from blender_texture_screen import walls

    path, centre, rows = entry
    likely = {r[0] for r in rows}
    findings = {}
    for side, wall in enumerate(walls(path)):
        if side in likely and (hits := matches(wall, centre)):
            findings[side] = hits
    return path.stem, findings


def configure():
    cv2.setNumThreads(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elevations", type=Path)
    parser.add_argument("probabilities", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    centres = json.loads((args.elevations / "centres.json").read_text())
    with gzip.open(args.probabilities) as stream:
        candidates = json.load(stream)
    entries = [
        (p, centres[p.stem], candidates[p.stem])
        for p in args.elevations.glob("*/*.png")
        if p.stem in candidates
    ]
    entries.sort(key=lambda e: (round(e[1][0] / CELL), round(e[1][1] / CELL)))
    result = {}
    with mp.get_context("spawn").Pool(args.workers, initializer=configure) as pool:
        for k, (key, match) in enumerate(
            pool.imap_unordered(confirm, entries, chunksize=24), 1
        ):
            if match:
                result[key] = match
            if k % 1000 == 0:
                print(
                    json.dumps({"geographic_checks": k, "matches": len(result)}),
                    flush=True,
                )
    with gzip.open(args.output, "wt") as out:
        json.dump(result, out, separators=(",", ":"))


if __name__ == "__main__":
    main()
