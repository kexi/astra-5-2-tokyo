# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.3.5", "pillow==12.1.1", "opencv-python-headless==4.11.0.86"]
# ///
"""Separate photographed tree crowns/shadows from the lawns and plaza beneath them."""

import hashlib
import json
import math
from pathlib import Path

import cv2 as cv
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
# Survey reference pixels in the 1344 × 832 overview of the fixed Z19 tile bounds.
# These traced lawns end inside their kerbs. The through road, paths, bridge,
# moat and palace buildings are never in the retouch mask.
LAWNS = [
    [
        (0, 401),
        (22, 395),
        (65, 395),
        (99, 384),
        (122, 306),
        (129, 296),
        (129, 327),
        (75, 529),
        (68, 535),
        (55, 535),
        (0, 518),
    ],
    [
        (223, 356),
        (231, 353),
        (245, 354),
        (350, 380),
        (247, 585),
        (239, 594),
        (219, 594),
        (149, 566),
        (140, 560),
        (138, 550),
        (140, 541),
    ],
    [
        (300, 212),
        (337, 219),
        (351, 226),
        (350, 240),
        (327, 314),
        (320, 321),
        (306, 324),
        (282, 320),
        (277, 313),
        (280, 297),
    ],
    [(372, 302), (399, 307), (387, 329), (377, 335), (363, 333)],
    [(397, 401), (433, 411), (424, 457), (405, 540), (337, 647), (282, 623)],
    [
        (443, 414),
        (477, 424),
        (487, 432),
        (455, 535),
        (444, 538),
        (423, 533),
        (422, 516),
    ],
    [(403, 556), (446, 561), (424, 686), (350, 653)],
    [
        (102, 643),
        (113, 642),
        (183, 672),
        (190, 681),
        (190, 699),
        (125, 818),
        (106, 824),
        (45, 803),
        (40, 789),
        (44, 773),
        (89, 658),
    ],
    [(247, 701), (305, 718), (302, 763), (311, 826), (175, 826), (228, 719)],
    [(316, 721), (395, 743), (397, 755), (380, 828), (325, 827), (312, 767)],
    [
        (437, 323),
        (465, 328),
        (530, 345),
        (520, 394),
        (481, 396),
        (437, 386),
        (428, 376),
    ],
]
# Two rows of individual trees are visible on the stone paving of the station square.
# Crown centres, measured on the photograph, are southwest of their baked shadows.
PLAZA = [
    (928.4, 373.8),
    (939.5, 376.1),
    (950.1, 378.4),
    (961.0, 381.0),
    (971.9, 383.5),
    (982.7, 386.1),
    (993.7, 388.6),
    (909.8, 441.7),
    (921.1, 444.7),
    (931.9, 447.1),
    (942.5, 450.0),
    (953.7, 453.5),
    (964.2, 455.5),
    (975.5, 458.9),
]
PLAINS = [
    [(926.2, 382.7), (967.5, 393.0), (963.7, 407.9), (922.8, 397.0)],
    [(916.6, 418.9), (957.9, 429.8), (954.4, 443.6), (911.9, 433.1)],
]
BOUNDS = [465677, 206446, 465698, 206459]
SIZE = 2  # working samples per overview pixel (~0.49 m/pixel)
SHAPE = (832 * SIZE, 1344 * SIZE)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def polygon_mask(polygons):
    canvas = np.zeros(SHAPE, np.uint8)
    for outline in polygons:
        cv.fillPoly(canvas, [np.rint(np.asarray(outline) * SIZE).astype(np.int32)], 255)
    return canvas


def disk(radius):
    return cv.getStructuringElement(cv.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))


def local_coordinates(px, py, origin):
    merc_x = BOUNDS[0] + (px + 0.5) / (SIZE * 64)
    merc_y = BOUNDS[1] + (py + 0.5) / (SIZE * 64)
    lon = merc_x / (2**19) * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * merc_y / (2**19)))))
    return (lon - origin[0]) * 90528, (lat - origin[1]) * 110953


def estimate_lawn_crowns(photo, zones, origin):
    gray = cv.cvtColor(photo, cv.COLOR_RGB2GRAY)
    smoothed = cv.GaussianBlur(gray, (0, 0), 0.85)
    envelope = cv.GaussianBlur(
        cv.morphologyEx(smoothed, cv.MORPH_CLOSE, disk(28)), (0, 0), 4
    )
    deficit = cv.subtract(envelope, smoothed)
    # Connected dark areas include both the crown and the photographed shadow. Select
    # their centre from the greener, brighter return rather than the shadow centroid.
    dark = ((deficit > 13) & (zones > 0)).astype(np.uint8)
    dark = cv.morphologyEx(dark, cv.MORPH_OPEN, disk(1))
    dark = cv.morphologyEx(dark, cv.MORPH_CLOSE, disk(2))
    count, labels, rects, _ = cv.connectedComponentsWithStats(dark)
    hue = (
        photo[:, :, 1].astype(np.float32)
        - 0.63 * photo[:, :, 0]
        - 0.37 * photo[:, :, 2]
    )
    foliage = cv.GaussianBlur(hue + 0.11 * gray, (0, 0), 1.5)
    individual = cv.GaussianBlur(gray, (0, 0), 8).astype(np.float32) - cv.GaussianBlur(
        gray, (0, 0), 1.8
    )
    local_peaks = (individual >= cv.dilate(individual, disk(6))) & (individual > 6)
    accepted = np.zeros_like(gray)
    sites = []
    for region in range(1, count):
        x, y, w, h, area = rects[region]
        if area < 12 or min(w, h) < 3:
            continue
        local = (labels[y : y + h, x : x + w] == region).astype(np.uint8)
        if area / (w * h) < 0.16:
            continue
        distances = cv.distanceTransform(np.pad(local, 1), cv.DIST_L2, 5)[1:-1, 1:-1]
        peaks = (distances == cv.dilate(distances, disk(6))) & (
            distances >= max(2.0, min(4.0, distances.max() * 0.5))
        )
        cluster = area > 3100 or max(w, h) > 121
        if cluster:
            peaks = local_peaks[y : y + h, x : x + w] & (local != 0)
        _, _, _, peaks_center = cv.connectedComponentsWithStats(peaks.astype(np.uint8))
        ranked = sorted(
            peaks_center[1:], key=lambda p: -distances[round(p[1]), round(p[0])]
        )
        picked = []
        for cx, cy in ranked:
            if any(math.hypot(cx - px, cy - py) < 10 for px, py in picked):
                continue
            picked.append((cx, cy))
        if not picked:
            continue
        # A solid hedge or a dark scrap along a kerb is not an individual tree.
        if (not cluster and area / len(picked) > 610) or (
            max(w, h) / min(w, h) > 4.5 and len(picked) < 2
        ):
            continue
        yy, xx = np.nonzero(local)
        scores = foliage[y + yy, x + xx]
        confirmed = np.zeros_like(local)
        for cx, cy in picked:
            near = (xx - cx) ** 2 + (yy - cy) ** 2 < 9**2
            if near.sum() < 6:
                continue
            weights = (
                np.maximum(0.01, scores[near] - np.percentile(scores[near], 35)) ** 1.3
            )
            px = float(np.average(xx[near], weights=weights)) + x
            py = float(np.average(yy[near], weights=weights)) + y
            local_area = area / len(picked)
            if cluster:
                crown_area = np.count_nonzero((xx - cx) ** 2 + (yy - cy) ** 2 < 12**2)
                local_area = 0.52 * local_area + 0.48 * crown_area
            radius = np.clip(0.49 * math.sqrt(local_area / (1.7 * math.pi)), 0.95, 4.3)
            sites.append((*local_coordinates(px, py, origin), float(radius), "lawn"))
            cv.circle(confirmed, (round(cx), round(cy)), 14, 255, -1)
        accepted[y : y + h, x : x + w] |= (
            (local * 255 & confirmed) if cluster else local * 255
        )
    return cv.bitwise_and(cv.dilate(accepted, disk(4)), zones), sites


def unoccluded_surface(photo, remove, zone):
    # Weighted neighbourhoods supply actual unshaded grass or paving from the same
    # planting bed. A high-frequency residual from clean pixels keeps the grain.
    known = (zone > 0) & (remove == 0)
    valid = known.astype(np.float32)
    rgb = photo.astype(np.float32)
    result = np.zeros_like(rgb)
    usable = np.zeros_like(valid, dtype=bool)
    for sigma in (4, 11, 27, 58):
        weights = cv.GaussianBlur(valid, (0, 0), sigma)
        choose = (weights > 0.06) & (~usable)
        for c in range(3):
            samples = cv.GaussianBlur(rgb[:, :, c] * valid, (0, 0), sigma)
            result[:, :, c][choose] = samples[choose] / weights[choose]
        usable |= choose
        if np.all(usable[remove > 0]):
            break
    # An offset sample supplies sub-metre variations without tiling a single patch.
    detail = cv.subtract(rgb, cv.GaussianBlur(rgb, (0, 0), 1.8))
    grain = np.zeros_like(rgb)
    filled = np.zeros_like(known)
    for dx, dy in ((19, 6), (-16, 13), (8, -22), (-35, -5), (21, 33)):
        candidate = np.roll(known, (dy, dx), axis=(0, 1)) & (~filled)
        grain[candidate] = np.roll(detail, (dy, dx), axis=(0, 1))[candidate]
        filled |= candidate
    result = np.clip(result + np.clip(grain, -9, 9) * 0.78, 0, 255).astype(np.uint8)
    alpha = cv.GaussianBlur(remove, (0, 0), 1.7)
    alpha[~usable | (zone == 0)] = 0
    return result, alpha


def retouch(photo, origin):
    lawn_zone = polygon_mask(LAWNS)
    removal, sites = estimate_lawn_crowns(photo, lawn_zone, origin)
    out = np.zeros((*SHAPE, 4), np.uint8)

    def apply(mask, zones):
        yy, xx = np.nonzero(mask)
        minx, maxx = max(0, int(xx.min()) - 100), min(SHAPE[1], int(xx.max()) + 101)
        miny, maxy = max(0, int(yy.min()) - 100), min(SHAPE[0], int(yy.max()) + 101)
        sub = (slice(miny, maxy), slice(minx, maxx))
        rgb, alpha = unoccluded_surface(photo[sub], mask[sub], zones[sub])
        target = out[sub]
        replace = alpha > target[:, :, 3]
        target[:, :, :3][replace] = rgb[replace]
        target[:, :, 3][replace] = alpha[replace]

    apply(removal, lawn_zone)
    plaza_zone = np.zeros(SHAPE, np.uint8)
    plaza_mask = np.zeros(SHAPE, np.uint8)
    gray = cv.cvtColor(photo, cv.COLOR_RGB2GRAY)
    for px, py in PLAZA:
        sites.append((*local_coordinates(px * SIZE, py * SIZE, origin), 2.85, "plaza"))
        region = np.zeros(SHAPE, np.uint8)
        center = (round((px + 1.0) * SIZE), round((py - 2.9) * SIZE))
        cv.ellipse(
            region, center, (round(5.0 * SIZE), round(7.7 * SIZE)), 26, 0, 360, 255, -1
        )
        selected = np.where((region > 0) & (gray < 145), 255, 0).astype(np.uint8)
        plaza_mask |= cv.dilate(selected, disk(2))
        cv.ellipse(
            plaza_zone,
            center,
            (round(16 * SIZE), round(14 * SIZE)),
            26,
            0,
            360,
            255,
            -1,
        )
    turf = polygon_mask(PLAINS)
    # Fill both sides of the two sharp grass/paving boundaries independently.
    apply(plaza_mask & ~turf, plaza_zone & ~turf)
    apply(plaza_mask & turf, plaza_zone & turf)
    return out, sites, lawn_zone, plaza_zone


def shade_roots(overlay, photo, placements, origin, lawn, plaza):
    rng = np.random.default_rng(53394611)
    height, width = lawn.shape
    # Restore variations at several turf scales inside the repaired grass only.
    grain = np.zeros(lawn.shape, np.float32)
    for pitch, contrast in ((3, 0.030), (10, 0.056), (33, 0.045)):
        lattice = rng.normal(0, 1, (height // pitch + 2, width // pitch + 2)).astype(
            np.float32
        )
        grain += contrast * cv.resize(
            lattice, (width, height), interpolation=cv.INTER_CUBIC
        )
    cover = (lawn > 0) & (overlay[:, :, 3] > 0)
    overlay[cover, :3] = np.clip(
        overlay[cover, :3] * (1 + grain[cover, None]), 0, 255
    ).astype(np.uint8)

    # Contact occlusion follows the model roots and has no solar direction.
    # The renderers separately cast directional shadows from the actual geometry.
    shade = np.zeros(lawn.shape, np.float32)
    metres = 90528 * 360 / (2**19 * SIZE * 64)
    for east, north, scale, *_ in placements:
        longitude, latitude = origin[0] + east / 90528, origin[1] + north / 110953
        px = ((longitude + 180) / 360 * 2**19 - BOUNDS[0]) * SIZE * 64 - 0.5
        py = (
            (1 - math.asinh(math.tan(math.radians(latitude))) / math.pi) * 2**18
            - BOUNDS[1]
        ) * SIZE * 64 - 0.5
        cx, cy = round(px), round(py)
        if not (0 <= cx < width and 0 <= cy < height) or not (
            lawn[cy, cx] or plaza[cy, cx]
        ):
            continue
        crown = max(0.9, 3.6 * scale)
        inner, outer = 0.19 + 0.13 * crown, 0.45 + 0.36 * crown
        reach = math.ceil(3.2 * outer / metres)
        x0, x1 = max(0, cx - reach), min(width, cx + reach + 1)
        y0, y1 = max(0, cy - reach), min(height, cy + reach + 1)
        yy, xx = np.ogrid[y0:y1, x0:x1]
        squared = ((xx - px) ** 2 + (yy - py) ** 2) * metres**2
        shadow = 0.25 * np.exp(-squared / (2 * inner**2)) + 0.15 * np.exp(
            -squared / (2 * outer**2)
        )
        np.maximum(shade[y0:y1, x0:x1], shadow, out=shade[y0:y1, x0:x1])
    contact = (shade >= 0.006) & ((lawn > 0) | (plaza > 0))
    original_alpha = overlay[contact, 3:4].astype(np.float32) / 255
    contact_alpha = np.clip(shade[contact, None] / 0.045, 0, 1)
    alpha = original_alpha + (1 - original_alpha) * contact_alpha
    surface = overlay[contact, :3] * original_alpha + photo[contact] * (
        1 - original_alpha
    )
    shaded = surface * (1 - shade[contact, None])
    overlay[contact, :3] = np.clip(
        (shaded - photo[contact] * (1 - alpha)) / alpha, 0, 255
    ).astype(np.uint8)
    overlay[contact, 3] = np.rint(alpha[:, 0] * 255).astype(np.uint8)


def main():
    survey = json.loads((ASSETS / "survey.json").read_text())
    source = next(
        layer for layer in survey["photos"] if layer["source"] == "metro-ortho"
    )
    if source["zoom"] != 19 or source["bounds"] != BOUNDS:
        raise ValueError(
            "Georeferenced lawn polygons need the Tokyo Station Z19 survey bounds"
        )
    path = ASSETS / source["file"]
    if digest(path) != source["sha256"]:
        raise ValueError("Tokyo Station survey photograph checksum mismatch")
    with Image.open(path) as image:
        photo = np.asarray(image.resize((SHAPE[1], SHAPE[0]), Image.Resampling.LANCZOS))
    overlay, sites, lawn, plaza = retouch(photo, survey["origin"])
    image_file = ASSETS / "park-ground.png"
    existing = json.loads((ROOT / "tools/tree-layout.json").read_text())
    surveyed = json.loads((ASSETS / "manifest.json").read_text()).get(
        "surveyedTrees", []
    )
    trees = []
    for x, y, radius, surface in sites:
        if any(
            (x - ex) ** 2 + (y - ny) ** 2 < max(2.6, radius * 0.95, scale * 3.0) ** 2
            for ex, ny, scale, _ in existing
        ):
            continue
        if any(
            (x - ex) ** 2 + (y - ny) ** 2 < max(2.6, radius, r) ** 2
            for ex, ny, r in surveyed
        ):
            continue
        if any(
            (x - row[0]) ** 2 + (y - row[1]) ** 2 < max(2.6, radius * 0.9) ** 2
            for row in trees
        ):
            continue
        trees.append([round(x, 2), round(y, 2), round(radius, 2), surface])
    placements = []
    for i, (east, north, radius, area) in enumerate(trees):
        # An apparent plan radius supports approximate geometry, not a surveyed height.
        angle = round((i * 2.39996323) % (2 * math.pi), 4)
        scale = round(radius / 3.6, 4)
        height = round(radius * (1.44 if area == "lawn" else 1.85) + 1.15, 2)
        placements.append([east, north, scale, angle, height, radius, area])
    planted = [
        tree
        for tree in [*existing, *placements]
        if not any(
            math.hypot(x - tree[0], y - tree[1]) < max(2.1, r * 0.8)
            for x, y, r in surveyed
        )
    ]
    shade_roots(overlay, photo, planted, survey["origin"], lawn, plaza)
    Image.fromarray(overlay).save(image_file, optimize=True)
    meta = {
        "version": 1,
        "layer": {
            "file": image_file.name,
            "zoom": 19,
            "bounds": BOUNDS,
            "width": SHAPE[1],
            "height": SHAPE[0],
            "sha256": digest(image_file),
            "bytes": image_file.stat().st_size,
        },
        "trees": placements,
        "coordinateOrder": [
            "east_m",
            "north_m",
            "horizontal_scale",
            "yaw_radians",
            "estimated_height_m",
            "apparent_crown_radius_m",
            "planting_area",
        ],
        "sourcePhoto": {
            "file": path.name,
            "sha256": digest(path),
            "source": "東京都デジタルツイン実現プロジェクト 区部オルソ写真を加工。欠測域は国土地理院の航空写真で補完。",
            "sources": [
                item
                for item in survey["sources"]
                if item["id"] in (source["source"], source["fillSource"])
            ],
        },
        "method": "Grass/paving sampled inside traced planting areas. Restored turf includes multiscale colour variation and local trunk contact occlusion. Directional shadows come from model geometry. Existing model trees are retained. Additional crown centres and radii are photographic estimates; species and heights are approximate.",
        "correctedPixels": int(np.count_nonzero(overlay[:, :, 3])),
        "detectedCrowns": len(sites),
        "additionalTrees": len(trees),
    }
    (ASSETS / "park-ground.json").write_text(
        json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    print(
        f"{len(sites)} photographed crowns, {len(trees)} additional tree instances; {meta['correctedPixels']} corrected pixels"
    )


if __name__ == "__main__":
    main()
