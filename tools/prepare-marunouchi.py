# /// script
# requires-python = ">=3.11"
# dependencies = ["laspy==2.6.1", "numpy==2.3.5", "pillow==12.1.1", "pyproj==3.7.2"]
# ///
"""Measure the Marunouchi 2-chome roof in Tokyo's 2024 LAS and rectify its 20 cm orthophoto."""

import argparse
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from shutil import copyfileobj
from urllib.request import urlopen

import laspy
import numpy as np
from PIL import Image, ImageFilter
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
SHEET = "09LD1884"
DATA = "https://gic-tokyo.s3.ap-northeast-1.amazonaws.com/2024/dig"
ORIGIN = np.array([-295.559, -172.600])
U = np.array([0.96667, -0.256])
U /= np.linalg.norm(U)
V = np.array([-U[1], U[0]])
SPAN = (35.5, 100.8)
PITCH = 0.2


def source(cache, layer):
    path = cache / f"{layer}-{SHEET}.zip"
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        with urlopen(f"{DATA}/{layer}/{SHEET}.zip", timeout=120) as response:
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as temp:
                copyfileobj(response, temp)
            Path(temp.name).replace(path)
    return path


def prepare(cache, work):
    work.mkdir(parents=True, exist_ok=True)
    origin = json.loads((ROOT / "assets/manifest.json").read_text())["origin"]
    geoid = json.loads((ROOT / "assets/rail-reference.json").read_text())[
        "verticalDatum"
    ]["geoidHeight"]
    lon, lat = np.radians(origin[:2])
    east = [-np.sin(lon), np.cos(lon), 0]
    up = np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    rotation = np.column_stack([east, np.cross(up, east), up])
    to_ecef = Transformer.from_crs(4979, 4978, always_xy=True)
    from_ecef = Transformer.from_crs(4978, 4979, always_xy=True)
    projected = Transformer.from_crs(4326, 6677, always_xy=True)
    geographic = Transformer.from_crs(6677, 4326, always_xy=True)
    ecef0 = np.array(to_ecef.transform(*origin))
    lidar, ortho = (source(cache, k) for k in ("lp", "ortho"))
    with zipfile.ZipFile(lidar) as archive, archive.open(f"{SHEET}.las") as file:
        las = laspy.read(file)
        # The full survey sheet covers several city blocks. This window includes the roof perimeter.
        subset = (
            (las.x > -6390)
            & (las.x < -6315)
            & (las.y > -35568)
            & (las.y < -35430)
            & (las.z > 22)
            & (las.z < 85)
        )
        x, y, z = (np.asarray(getattr(las, axis))[subset] for axis in ("x", "y", "z"))
    lon, lat = geographic.transform(x, y)
    points = (
        np.column_stack(to_ecef.transform(lon, lat, z + geoid)) - ecef0
    ) @ rotation
    plan = (points[:, :2] - ORIGIN) @ np.column_stack((U, V))
    keep = (
        (plan[:, 0] >= 0)
        & (plan[:, 0] < SPAN[0])
        & (plan[:, 1] >= 0)
        & (plan[:, 1] < SPAN[1])
    )
    s, t = plan[keep].T
    height = points[keep, 2]
    fans = []
    for box in [(20.7, 27.1, 43.5, 49.5), (20.7, 27.1, 37.0, 41.0)]:
        left, right, bottom, top = box
        region = (
            (s > left)
            & (s < right)
            & (t > bottom)
            & (t < top)
            & (height > 42.05)
            & (height < 43.05)
        )
        xy = np.column_stack((s[region], t[region]))
        center = np.median(xy, axis=0)
        radius = float(np.percentile(np.linalg.norm(xy - center, axis=1), 90))
        fans.append(
            {
                "center": center.round(3).tolist(),
                "radius": round(radius, 3),
                "z": round(float(np.median(height[region])), 3),
                "returns": int(region.sum()),
            }
        )
    plinths = []
    for bottom, top in [(23.0, 31.5), (37.0, 50.4), (53.1, 66.7)]:
        left, right = 20.85, 26.65
        region = (s > left) & (s < right) & (t > bottom) & (t < top)
        plinths.append(
            {
                "bounds": [left, bottom, right, top],
                "z": round(float(np.median(height[region])), 3),
                "returns": int(region.sum()),
            }
        )
    width, rows = (round(a / PITCH) for a in SPAN)
    tt, ss = np.indices((rows, width), dtype=float)
    ss = (ss + 0.5) * PITCH
    tt = (rows - tt - 0.5) * PITCH
    # Both circular rims lie ~0.7 m east and 2.1 m south in the orthophoto relative
    # to their elevated LAS returns. Rectify this local roof relief displacement.
    xy = ORIGIN + (ss + 0.71)[:, :, None] * U + (tt - 2.10)[:, :, None] * V
    xyz = (
        np.column_stack((xy.reshape(-1, 2), np.zeros(rows * width))) @ rotation.T
        + ecef0
    )
    ll = from_ecef.transform(*xyz.T)
    gx, gy = projected.transform(*ll[:2])
    with zipfile.ZipFile(ortho) as archive:
        name = next(n for n in archive.namelist() if n.lower().endswith(".tif"))
        with archive.open(name) as file:
            image = np.array(Image.open(file).convert("RGB"))
        wf = next(n for n in archive.namelist() if n.lower().endswith(".tfw"))
        dx, _, _, dy, x0, y0 = map(float, archive.read(wf).split())
    col = np.rint((gx - x0) / dx).astype(int).clip(0, image.shape[1] - 1)
    row = np.rint((gy - y0) / dy).astype(int).clip(0, image.shape[0] - 1)
    rgb = image[row, col].reshape(rows, width, 3)
    # A measured fan is built as geometry. Repair its photographic imprint on the
    # supporting metal sheet using the neighbouring, unoccupied strip of that sheet.
    for fan in fans:
        a, b = fan["center"]
        mask = (ss - a) ** 2 + (tt - b) ** 2 < (fan["radius"] + 0.38) ** 2
        nearby = rgb[(ss > 20.9) & (ss < 21.55) & (abs(tt - b) < 1.35)]
        rgb[mask] = np.median(nearby, axis=0)
    metal = Image.fromarray(rgb)
    # Only the central equipment strip is sampled by the model.
    crop = (
        int(20.4 / PITCH),
        int((SPAN[1] - 67.5) / PITCH),
        int(27.1 / PITCH),
        int((SPAN[1] - 22.5) / PITCH),
    )
    metal.crop(crop).resize((136, 900), Image.Resampling.BICUBIC).filter(
        ImageFilter.UnsharpMask(radius=0.9, percent=35, threshold=4)
    ).save(work / "equipment.png")

    # A seamless finish for the narrow projecting cladding panels. Individual grain
    # is below the sampling distance of the surveys and is deliberately reconstructed.
    rng = np.random.default_rng(11408)
    fine = rng.normal(0, 5.1, (256, 256))
    flecks = rng.choice([0, -17, 13], (256, 256), p=[0.88, 0.075, 0.045])
    stone = np.clip(np.array([155, 151, 144]) + (fine + flecks)[:, :, None], 0, 255)
    Image.fromarray(stone.astype("uint8")).save(work / "spandrel.png")

    def proof(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    record = {
        "origin": ORIGIN.tolist(),
        "u": U.tolist(),
        "v": V.tolist(),
        "fans": fans,
        "plinths": plinths,
        "crop": [
            crop[0] * PITCH,
            SPAN[1] - crop[3] * PITCH,
            crop[2] * PITCH,
            SPAN[1] - crop[1] * PITCH,
        ],
        "pointCount": int(keep.sum()),
        "sha256": {"lp": proof(lidar), "ortho": proof(ortho)},
    }
    (work / "roof.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument(
        "--cache",
        type=Path,
        default=Path(tempfile.gettempdir()) / "tokyo-survey-source",
    )
    args.add_argument("--work", type=Path, required=True)
    params = args.parse_args()
    prepare(params.cache, params.work)
