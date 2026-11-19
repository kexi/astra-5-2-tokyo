# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.3.5", "opencv-python-headless==4.11.0.86"]
# ///
"""Screen measured wall elevations for irregular aerial imagery and select same-building donors."""

import argparse
import gzip
import json
import multiprocessing as mp
import zlib
from pathlib import Path

import cv2
import numpy as np
from blender_texture_towers import TOWER_MASKS

FOREST = None
SIDE = 160


def walls(path):
    encoded = cv2.imread(str(path), cv2.IMREAD_COLOR)
    for side in range(4):
        view = encoded[:, side * SIDE : (side + 1) * SIDE]
        visible = np.any(view > 0, axis=2)
        ys, xs = np.nonzero(visible)
        if len(xs) < 60:
            yield None
            continue
        yield view[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def descriptors(wall):
    h, w = wall.shape[:2]
    step = 12
    radius = min(14, max(4, (min(h, w) - 1) // 2))
    xs = np.arange(radius, w - radius, step)
    ys = np.arange(radius, h - radius, step)
    if not len(xs) or not len(ys):
        return None
    x, y = np.meshgrid(xs, ys)
    index = np.s_[ys[:, None], xs]
    visible = (np.max(wall, axis=2) > 0).astype("f4")
    gray = cv2.cvtColor(wall, cv2.COLOR_BGR2GRAY).astype("f4")
    g = gray / 255
    fields = [visible, g, g * g]
    b, gr, r = cv2.split(wall.astype("f4") / 255)
    fields.extend(
        (gr - r, b - r, np.maximum.reduce([b, gr, r]) - np.minimum.reduce([b, gr, r]))
    )
    for sigma in (0.7, 2.0):
        tone = cv2.GaussianBlur(g, (0, 0), sigma)
        horizontal = cv2.Sobel(tone, cv2.CV_32F, 1, 0)
        vertical = cv2.Sobel(tone, cv2.CV_32F, 0, 1)
        magnitude, angle = cv2.cartToPolar(horizontal, vertical, angleInDegrees=True)
        orientation = ((angle + 11.25) % 180 // 22.5).astype("u1")
        fields.extend((magnitude, magnitude * magnitude))
        for j in range(8):
            fields.append((orientation == j) * magnitude)
        fields.extend(
            (
                np.minimum(abs(horizontal), abs(vertical)),
                np.maximum(abs(horizontal), abs(vertical)),
            )
        )
    for distance in (2, 5, 9):
        fields.append(abs(g - np.roll(g, distance, axis=0)))
        fields.append(abs(g - np.roll(g, distance, axis=1)))
    # Local colour and energy are relative to the candidate building's own
    # visible photographic surface, to reduce sensitivity to shadow/exposure.
    field = cv2.merge([f.astype("f4") for f in fields])
    hist = cv2.blur(field, (2 * radius + 1, 2 * radius + 1))[index]
    local = hist[:, :, 1:2]
    spread = np.sqrt(np.maximum(0, hist[:, :, 2:3] - local * local))
    average = g[visible > 0].mean()
    angles = hist[:, :, 8:16]
    anisotropy = angles / np.maximum(0.0003, angles.sum(2, keepdims=True))
    feature = np.concatenate(
        (hist[:, :, 1:], spread, local - average, anisotropy), axis=2
    )
    return (
        feature,
        hist[:, :, 0] > 0.91,
        (x + 0.5) / w,
        (y + 0.5) / h,
        (radius / w, radius / h),
    )


def inside(x, y, rect, margin=0):
    l, t, r, b = rect
    return (
        (x >= l + margin * (r - l))
        & (x <= r - margin * (r - l))
        & (y >= t + margin * (b - t))
        & (y <= b - margin * (b - t))
    )


def holdout(identifier):
    return zlib.crc32(identifier.encode()) % 5 == 0


def train(files, directory):
    samples = [[], []]
    rng = np.random.default_rng(1719)
    for identifier, ((donor, patch), masks) in TOWER_MASKS.items():
        if holdout(identifier):
            continue
        for side, panel in enumerate(walls(files[identifier])):
            if panel is None or (d := descriptors(panel)) is None:
                continue
            features, eligible, x, y, _ = d
            flags = np.zeros(x.shape, bool)
            for rect in masks.get(side, ()):
                flags |= inside(x, y, rect, 0.13)
            negative = (
                inside(x, y, patch, 0.04) if side == donor else np.zeros(x.shape, bool)
            )
            # Unmarked portions are not automatically assumed to be healthy.
            for label, mask in enumerate((negative, flags & ~negative)):
                candidates = features[mask & eligible]
                if len(candidates):
                    indices = rng.choice(
                        len(candidates), min(30, len(candidates)), replace=False
                    )
                    samples[label].extend(candidates[indices])
    count = min(map(len, samples))
    data = np.vstack(
        [np.array(s)[rng.choice(len(s), count, False)] for s in samples]
    ).astype("f4")
    labels = np.repeat(np.arange(2, dtype="i4"), count)
    cv2.setRNGSeed(1719)
    forest = cv2.ml.RTrees_create()
    forest.setMaxDepth(11)
    forest.setMinSampleCount(7)
    forest.setTermCriteria((cv2.TERM_CRITERIA_MAX_ITER, 96, 0))
    forest.train(data, cv2.ml.ROW_SAMPLE, labels)
    forest.save(str(directory / "wall-samples.xml"))
    return {
        "source_negative": len(samples[0]),
        "source_positive": len(samples[1]),
        "used_per_class": count,
    }


def init_worker(model):
    global FOREST
    cv2.setNumThreads(1)
    FOREST = cv2.ml.RTrees_load(str(model))


def patches(panel):
    if panel is None or (d := descriptors(panel)) is None:
        return None
    f, eligible, x, y, half = d
    votes = FOREST.getVotes(f.reshape(-1, f.shape[2]), 0)[1:]
    probability = (votes[:, 1] / np.maximum(1, votes.sum(1))).reshape(x.shape)
    return probability, eligible, x, y, half


def regions(data, min_score):
    if data is None:
        return []
    prob, valid, x, y, (rx, ry) = data
    weak = (prob >= min_score - 0.12) & valid
    n, labels, _, _ = cv2.connectedComponentsWithStats(
        weak.astype("u1"), connectivity=4
    )
    out = []
    for k in range(1, n):
        take = labels == k
        values = prob[take]
        core = np.sum(values >= min_score)
        if core < 2 or len(values) < 3:
            continue
        bounds = (
            max(0, float(x[take].min() - rx)),
            max(0, float(y[take].min() - ry)),
            min(1, float(x[take].max() + rx)),
            min(1, float(y[take].max() + ry)),
        )
        out.append((round(float(np.sort(values)[-2]), 4), bounds))
    return out


def donor_region(data):
    if data is None:
        return None
    prob, valid, x, y, (rx, ry) = data
    best = None
    for j, i in zip(*np.nonzero(valid & (prob < 0.24)), strict=True):
        around = (abs(x - x[j, i]) < rx * 1.7) & (abs(y - y[j, i]) < ry * 1.7)
        if not valid[around].all() or np.max(prob[around]) > 0.35:
            continue
        q = 1 - float(prob[around].mean())
        box = (
            max(0.01, float(x[j, i] - rx)),
            max(0.01, float(y[j, i] - ry)),
            min(0.99, float(x[j, i] + rx)),
            min(0.99, float(y[j, i] + ry)),
        )
        if best is None or q > best[0]:
            best = q, box
    return best


def scan(path):
    ds = [patches(w) for w in walls(path)]
    donors = [donor_region(d) for d in ds]
    out = []
    for side, d in enumerate(ds):
        for score, bounds in regions(d, 0.86):
            candidates = [
                (quality + 0.12 * (j == side), j, crop)
                for j, entry in enumerate(donors)
                if entry
                for quality, crop in [entry]
            ]
            if not candidates:
                out.append((side, bounds, None, None, score))
                continue
            _, j, crop = max(candidates)
            out.append((side, bounds, j, crop, score))
    return path.stem, out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elevations", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--ids", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    files = {p.stem: p for p in args.elevations.glob("*/*.png")}
    model = args.output / "wall-samples.xml"
    if not model.exists():
        metrics = train(files, args.output)
        (args.output / "training.json").write_text(json.dumps(metrics))
    wanted = set(files) if args.ids is None else set(args.ids.read_text().splitlines())
    selected = sorted(p for i, p in files.items() if i in wanted)
    result = {}
    with mp.get_context("spawn").Pool(
        args.workers, initializer=init_worker, initargs=(model,)
    ) as workers:
        for k, (i, rows) in enumerate(
            workers.imap_unordered(scan, selected, chunksize=16), 1
        ):
            if rows:
                result[i] = rows
            if k % 1000 == 0:
                print(json.dumps({"screened": k, "flagged": len(result)}), flush=True)
    with gzip.open(args.output / "screen.json.gz", "wt") as f:
        json.dump(dict(sorted(result.items())), f, separators=(",", ":"))
    (args.output / "totals.json").write_text(
        json.dumps({"screened": len(selected), "with_candidate": len(result)})
    )


if __name__ == "__main__":
    main()
