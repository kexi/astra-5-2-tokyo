# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.3.5", "scipy==1.16.3", "mapbox-earcut==1.0.3"]
# ///
"""Fit and independently recheck the rigid registration of the 2023 underground model."""

import argparse
import itertools
import json
import math
import runpy
import struct
import tempfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np
from scipy.spatial import cKDTree

BUILDER = runpy.run_path(str(Path(__file__).with_name("prepare-omy-underground.py")))
URL = "https://www.geospatial.jp/ckan/dataset/eb79c194-3f26-4c41-9b48-6664f4684ebc/resource/d441c885-6a15-476b-836d-cddd9a5fa006/download/shapefile.zip"
SHA = "c7a79ea9e141721cd6f9dba443ba9bd9120a0bcba025597b83e755a6ab3acec1"
HOLDOUT = ("ChiyodaNiju", "MaruTokyo", "HibiyaHibiya")
ORIGIN = np.array(
    json.loads((BUILDER["ASSETS"] / "manifest.json").read_text())["origin"][:2]
)
METRES = BUILDER["METRES"]


def shapefile_rings(data):
    end = 100
    while end < len(data):
        _, size = struct.unpack_from(">ii", data, end)
        pos = end + 8
        end = pos + size * 2
        shape = struct.unpack_from("<i", data, pos)[0]
        if shape == 0:
            continue
        if shape != 5:
            raise ValueError("Expected a floor polygon")
        parts, n = struct.unpack_from("<2i", data, pos + 36)
        bounds = [*np.frombuffer(data, "<i4", parts, pos + 44), n]
        xy = np.frombuffer(data, "<f8", n * 2, pos + 44 + parts * 4).reshape(-1, 2)
        for a, b in itertools.pairwise(bounds):
            yield (xy[a:b] - ORIGIN) * METRES


def floor_rings(source):
    with source.open(BUILDER["GML"]) as stream:
        floor = False
        for event, elem in ET.iterparse(stream, events=("start", "end")):
            tag = elem.tag.rsplit("}", 1)[-1]
            if tag in ("FloorSurface", "OuterFloorSurface"):
                floor = event == "start"
            if event == "end":
                if floor and tag == "posList":
                    xyz = np.fromstring(elem.text, sep=" ").reshape(-1, 3)
                    yield (xyz[:, [1, 0]] - ORIGIN) * METRES
                elem.clear()


def corners(rings):
    points = []
    directions = []
    for ring in rings:
        p = ring[:-1]
        a = np.roll(p, 1, axis=0) - p
        b = np.roll(p, -1, axis=0) - p
        la = np.linalg.norm(a, axis=1)
        lb = np.linalg.norm(b, axis=1)
        # Very short edges, straight runs and shallow bends give ambiguous ties.
        keep = (
            (la > 1.2)
            & (lb > 1.2)
            & (abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) > 0.45 * la * lb)
        )
        points.extend(p[keep])
        directions.extend(np.stack((a[keep], b[keep]), axis=1))
    return np.array(points), np.array(directions)


def rotation(a):
    return np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path(tempfile.gettempdir()) / "omy-uc23-underground.zip",
    )
    parser.add_argument(
        "--indoor",
        type=Path,
        default=Path(tempfile.gettempdir()) / "omy-official-indoor.zip",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="update the registration input used by the GLB builder",
    )
    args = parser.parse_args()
    with BUILDER["checked_archive"](
        args.archive, BUILDER["URL"], BUILDER["SHA256"]
    ) as source:
        p, v = corners(floor_rings(source))
    # A coarse registration of the rasterized floor silhouettes initializes the
    # local correspondence search. The fitted coefficients use individual corners.
    rough = rotation(math.radians(79.3))
    tree = cKDTree(p @ rough.T + [-926.87, -817.66])
    rays = v @ rough.T
    by_file = defaultdict(list)
    BUILDER["checked_archive"](args.indoor, URL, SHA).close()
    with ZipFile(args.indoor, metadata_encoding="cp932") as ref:
        for name in ref.namelist():
            if "/2.地下鉄_公共通路/B" not in name or not name.endswith("_Floor.shp"):
                continue
            points, directions = corners(shapefile_rings(ref.read(name)))
            for point, dirs in zip(points, directions):
                opts = []
                tolerance = np.minimum(0.75, 0.13 * np.linalg.norm(dirs, axis=1))
                for k in tree.query_ball_point(point, 5.0):
                    for oriented in (rays[k], rays[k][::-1]):
                        delta = np.linalg.norm(oriented - dirs, axis=1)
                        if np.all(delta < tolerance):
                            opts.append((delta.mean(), k))
                if opts:
                    by_file[Path(name).name].append((p[min(opts)[1]], point))
    training = [
        pair
        for k, rows in by_file.items()
        if not k.startswith(HOLDOUT)
        for pair in rows
    ]
    x, y = map(np.array, zip(*training))
    a, b = x - x.mean(axis=0), y - y.mean(axis=0)
    theta = math.atan2(np.sum(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]), np.sum(a * b))
    rot = rotation(theta)
    shift = y.mean(axis=0) - x.mean(axis=0) @ rot.T

    def residual(rows):
        x, y = map(np.array, zip(*rows))
        errors = np.linalg.norm(x @ rot.T + shift - y, axis=1)
        return {
            "points": len(x),
            "medianM": round(float(np.median(errors)), 3),
            "p90M": round(float(np.percentile(errors, 90)), 3),
            "maxM": round(float(errors.max()), 3),
        }

    check = [
        pair for k, rows in by_file.items() if k.startswith(HOLDOUT) for pair in rows
    ]
    info = {
        "rotationDegrees": math.degrees(theta),
        "translationEastNorth": shift.tolist(),
        "reference": URL,
        "referenceZipSha256": SHA,
        "withheldPrefixes": HOLDOUT,
        "training": residual(training),
        "withheld": residual(check),
        "byReferenceFile": {k: residual(rows) for k, rows in sorted(by_file.items())},
    }
    text = json.dumps(info, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        Path(__file__).with_name("omy-underground-alignment.json").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
