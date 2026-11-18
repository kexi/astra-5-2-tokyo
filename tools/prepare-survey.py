# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.3.5", "pillow==12.1.1", "rasterio==1.4.3", "pyproj==3.7.2", "laspy==2.6.1"]
# ///
"""Build the Tokyo Station survey patch from bounded public map sheets."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

import laspy
import numpy as np
from PIL import Image, ImageFilter
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from rasterio.warp import reproject, transform_bounds

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
CACHE = Path(tempfile.gettempdir()) / "tokyo-survey-source"
# Original 1:500 map sheets; each covers 400 m east-west and 300 m north-south.
SHEETS = [f"09LD18{row}{col}" for row in range(6, 10) for col in range(2, 6)]
LAS_SHEETS = ["09LD1873", "09LD1884"]
DIG = "https://gic-tokyo.s3.ap-northeast-1.amazonaws.com/2024/dig"
GSI = "https://cyberjapandata.gsi.go.jp/xyz"
GIC_PAGE = "https://www.geospatial.jp/ckan/dataset/tokyopc-23ku-2024"
METRO_PAGE = "https://catalog.data.metro.tokyo.lg.jp/dataset/t000029d0000000024"
WORLD = 20037508.342789244
INVALID = -32767.0
NO_DATA_RGB = 0x800000
manifest = json.loads((ASSETS / "manifest.json").read_text())
ORIGIN = manifest["origin"]
# Local x and northing in metres in the project's existing station frame.
CORE = (-964, -320, 285, 430)
OUTER = (-1630, -690, 735, 780)
to_plane = Transformer.from_crs("EPSG:4326", "EPSG:6677", always_xy=True)


def fetch(url, name):
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / name
    if not target.exists():
        for attempt in range(3):
            try:
                with (
                    urlopen(url, timeout=80) as response,
                    tempfile.NamedTemporaryFile(dir=CACHE, delete=False) as tmp,
                ):
                    shutil.copyfileobj(response, tmp)
                Path(tmp.name).replace(target)
                break
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(0.8 * (attempt + 1))
    return target


def file_proof(path):
    return {
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def get_sheet(job):
    layer, code = job
    url = f"{DIG}/{layer}/{code}.zip"
    path = fetch(url, f"{layer}-{code}.zip")
    return (
        layer,
        code,
        path,
        {"layer": layer, "sheet": code, "url": url, **file_proof(path)},
    )


def xy_tile(lon, lat, zoom):
    return (
        (lon + 180) / 360 * 2**zoom,
        (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) * 2 ** (zoom - 1),
    )


def tile_bounds(area, zoom):
    west, south, east, north = area
    x0, y0 = xy_tile(west, north, zoom)
    x1, y1 = xy_tile(east, south, zoom)
    return (math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1))


def local_bounds(area):
    return (
        ORIGIN[0] + area[0] / 90528,
        ORIGIN[1] + area[1] / 110953,
        ORIGIN[0] + area[2] / 90528,
        ORIGIN[1] + area[3] / 110953,
    )


def output_grid(bounds, zoom, divisor=1):
    left, top, right, bottom = bounds
    tile = 2 * WORLD / 2**zoom
    affine = from_bounds(
        left * tile - WORLD,
        WORLD - bottom * tile,
        right * tile - WORLD,
        WORLD - top * tile,
        (right - left) * 256 // divisor,
        (bottom - top) * 256 // divisor,
    )
    return affine, ((bottom - top) * 256 // divisor, (right - left) * 256 // divisor)


def height_rgb(elevation):
    missing = (~np.isfinite(elevation)) | (elevation == INVALID)
    cm = np.rint(np.where(missing, 0, elevation) * 100).astype(np.int32) & 0xFFFFFF
    cm[missing] = NO_DATA_RGB
    return np.stack([cm >> 16, cm >> 8, cm], axis=2).astype(np.uint8)


def height_record(file, zoom, bounds, width, height, **extra):
    return {
        "file": file,
        "zoom": zoom,
        "bounds": list(bounds),
        "width": width,
        "height": height,
        "encoding": "GSI signed RGB24 centimetres; 0x800000 = no data",
        "vertical": "survey elevation in metres above sea level (Japanese national height datum); not ellipsoidal",
        **extra,
    }


def read_tiff(archive):
    with zipfile.ZipFile(archive) as packed:
        name = next(n for n in packed.namelist() if n.lower().endswith(".tif"))
        with MemoryFile(packed.read(name)) as mem, mem.open() as ds:
            assert ds.crs.to_epsg() == 6677 or "6677" in ds.crs.to_wkt()
            return ds.read(), ds.transform, ds.nodata


def prefecture_raster(layer, files, fallback):
    # Use complete native sheets in a single mosaic so resampling across shared edges is continuous.
    is_photo = layer == "ortho"
    width, height = (2000, 1500) if is_photo else (1600, 1200)
    bands = 3 if is_photo else 1
    mosaic = np.full(
        (bands, 4 * height, 4 * width),
        255 if is_photo else INVALID,
        np.uint8 if is_photo else np.float32,
    )
    source_grid = from_bounds(-7200, -36000, -5600, -34800, 4 * width, 4 * height)
    nodata = 255 if is_photo else INVALID
    for code in SHEETS:
        tile, grid, _ = read_tiff(files[layer, code])
        col = round((grid.c - source_grid.c) / source_grid.a)
        row = round((grid.f - source_grid.f) / source_grid.e)
        mosaic[:, row : row + tile.shape[1], col : col + tile.shape[2]] = tile
    zoom = 19 if is_photo else 17
    bounds = tile_bounds(local_bounds(CORE), zoom)
    grid, shape = output_grid(bounds, zoom)
    output = np.zeros((bands, *shape), mosaic.dtype)
    reproject(
        mosaic,
        output,
        src_transform=source_grid,
        src_crs="EPSG:6677",
        src_nodata=nodata,
        dst_transform=grid,
        dst_crs="EPSG:3857",
        dst_nodata=0 if is_photo else INVALID,
        resampling=Resampling.bilinear,
        num_threads=4,
    )
    if is_photo:
        # Survey sheets around the palace contain a solid white cutout with no TIFF
        # mask. Check all channels so pale road markings and coloured roofs survive.
        mask = np.any(output > 2, axis=0) & np.any(output < 246, axis=0)
    else:
        mask = output[0] != INVALID
    coverage = round(float(mask.mean()), 6)
    with Image.open(ASSETS / fallback["file"]) as im:
        background = np.asarray(im).copy()
    if is_photo:
        background = background.transpose(2, 0, 1)
    else:
        packed = (
            (background[..., 0].astype(np.int32) << 16)
            + (background[..., 1].astype(np.int32) << 8)
            + background[..., 2]
        )
        background = np.where(
            packed == NO_DATA_RGB,
            INVALID,
            np.where(packed >= NO_DATA_RGB, packed - 0x1000000, packed) * 0.01,
        ).astype(np.float32)[None, :, :]
    back_grid, _ = output_grid(fallback["bounds"], fallback["zoom"])
    fill = np.empty_like(output)
    reproject(
        background,
        fill,
        src_transform=back_grid,
        src_crs="EPSG:3857",
        src_nodata=None if is_photo else INVALID,
        dst_transform=grid,
        dst_crs="EPSG:3857",
        dst_nodata=0 if is_photo else INVALID,
        resampling=Resampling.bilinear,
        num_threads=4,
    )
    output[:, ~mask] = fill[:, ~mask]
    if is_photo:
        coverage_image = Image.fromarray(mask.astype(np.uint8) * 255)
        inner = coverage_image.filter(ImageFilter.MinFilter(7))
        transition = (
            np.asarray(inner.filter(ImageFilter.GaussianBlur(13)), dtype=np.float32)
            / 255
        )
        blend = np.clip((transition - 0.51) * 2.5, 0, 1)
        for channel in range(3):
            output[channel] = np.rint(
                output[channel] * blend + fill[channel] * (1 - blend)
            ).astype(np.uint8)
        file = "survey-tokyo-ortho.jpg"
        Image.fromarray(output.transpose(1, 2, 0)).save(
            ASSETS / file, quality=89, subsampling=0, optimize=True
        )
        return {
            "file": file,
            "zoom": zoom,
            "bounds": list(bounds),
            "width": shape[1],
            "height": shape[0],
            "source": "metro-ortho",
            "sourceResolutionM": 0.2,
            "metroFraction": coverage,
            "fillSource": "gsi-photo",
        }
    file = "survey-tokyo-dem.png"
    Image.fromarray(height_rgb(output[0])).save(ASSETS / file, optimize=True)
    valid = output[0] != INVALID
    return height_record(
        file,
        zoom,
        bounds,
        shape[1],
        shape[0],
        source="metro-grd25",
        sourceResolutionM=0.25,
        validFraction=round(float(valid.mean()), 6),
        metroFraction=coverage,
        fillSource="gsi-dem5a",
    )


def gsi_mosaic(layer, zoom, photo):
    area = local_bounds(OUTER)
    bounds = tile_bounds(area, zoom)
    left, top, right, bottom = bounds
    ext = "jpg" if photo else "png"
    result = Image.new("RGB", ((right - left) * 256, (bottom - top) * 256))
    cells = [(x, y) for y in range(top, bottom) for x in range(left, right)]

    def tile(cell):
        x, y = cell
        url = f"{GSI}/{layer}/{zoom}/{x}/{y}.{ext}"
        path = fetch(url, f"{layer}-{zoom}-{x}-{y}.{ext}")
        with Image.open(path) as im:
            return x, y, im.convert("RGB")

    with ThreadPoolExecutor(8) as pool:
        for x, y, image in pool.map(tile, cells):
            result.paste(image, ((x - left) * 256, (y - top) * 256))
    file = "survey-gsi-ortho.jpg" if photo else "survey-gsi-dem.png"
    if photo:
        result.save(ASSETS / file, quality=89, optimize=True)
        return {
            "file": file,
            "zoom": zoom,
            "bounds": list(bounds),
            "width": result.width,
            "height": result.height,
            "source": "gsi-photo",
            "tileCount": len(cells),
        }
    result.save(ASSETS / file, optimize=True)
    return height_record(
        file,
        zoom,
        bounds,
        result.width,
        result.height,
        source="gsi-dem5a",
        sourceResolutionM=5,
        tileCount=len(cells),
    )


def point_surface(x, y, z, sheet):
    # Upper decile, not the maximum: isolated high returns should not raise the surface.
    west, south = math.floor(x.min() / 400) * 400, math.floor(y.min() / 300) * 300
    east, north = west + 400, south + 300
    col = np.clip(np.floor(x - west).astype(np.int32), 0, 399)
    row = np.clip(np.floor(north - y).astype(np.int32), 0, 299)
    flat = row * 400 + col
    order = np.lexsort((z, flat))
    keys, starts, counts = np.unique(flat[order], return_index=True, return_counts=True)
    surface = np.full(120000, INVALID, np.float32)
    surface[keys] = z[order[starts + np.floor(0.90 * (counts - 1)).astype(np.int64)]]
    surface[keys[counts < 3]] = INVALID
    bounds = tile_bounds(
        transform_bounds("EPSG:6677", "EPSG:4326", west, south, east, north), 17
    )
    grid, shape = output_grid(bounds, 17)
    dst = np.full(shape, INVALID, np.float32)
    reproject(
        surface.reshape(300, 400),
        dst,
        src_transform=from_bounds(west, south, east, north, 400, 300),
        src_crs="EPSG:6677",
        src_nodata=INVALID,
        dst_transform=grid,
        dst_crs="EPSG:3857",
        dst_nodata=INVALID,
        resampling=Resampling.nearest,
    )
    file = f"survey-surface-{sheet.lower()}.png"
    Image.fromarray(height_rgb(dst)).save(ASSETS / file, optimize=True)
    return height_record(
        file,
        17,
        bounds,
        shape[1],
        shape[0],
        source="metro-las",
        sheet=sheet,
        statistic="90th percentile of returns per 1 m cell, minimum three returns; includes roofs, vegetation and decks",
    )


def canopy_heights(positions, height_list, points, x, y, z, dem, dem_grid):
    # Restrict candidates to the existing photo-detected trees and the verified park side of
    # the moat. Roofs are not filtered by height alone: require multiple lidar echoes,
    # a vertical distribution within the crown and a measurable fraction of green returns.
    west, south = math.floor(x.min() / 400) * 400, math.floor(y.min() / 300) * 300
    east, north = west + 400, south + 300
    binsize = 5
    ncol = 80
    bx = np.clip(((x - west) / binsize).astype(np.int32), 0, 79)
    by = np.clip(((y - south) / binsize).astype(np.int32), 0, 59)
    cell = by * ncol + bx
    order = np.argsort(cell, kind="stable")
    boundary = np.searchsorted(cell[order], np.arange(4801))
    multireturn = np.asarray(points.number_of_returns) > 1
    red, green, blue = (
        np.asarray(getattr(points, c), dtype=np.float32)
        for c in ("red", "green", "blue")
    )
    greener = (green > red * 1.012) & (green > blue * 1.08)
    for i, (tx, northing, size, angle) in enumerate(positions):
        px, py = to_plane.transform(
            ORIGIN[0] + tx / 90528, ORIGIN[1] + northing / 110953
        )
        if not west + 5 < px < min(east - 5, -6500) or not south + 5 < py < north - 5:
            continue
        cx, cy = int((px - west) / binsize), int((py - south) / binsize)
        keys = [
            cy2 * ncol + cx2
            for cy2 in range(cy - 1, cy + 2)
            for cx2 in range(cx - 1, cx + 2)
        ]
        indices = np.concatenate([order[boundary[k] : boundary[k + 1]] for k in keys])
        indices = indices[
            (x[indices] - px) ** 2 + (y[indices] - py) ** 2
            < min(4.7, max(2.9, size * 3.7)) ** 2
        ]
        if not len(indices):
            continue
        col = (x[indices] - dem_grid.c) / dem_grid.a
        row = (y[indices] - dem_grid.f) / dem_grid.e
        ground = dem[
            np.clip(row.astype(int), 0, dem.shape[0] - 1),
            np.clip(col.astype(int), 0, dem.shape[1] - 1),
        ]
        rel = z[indices] - ground
        canopy = (ground != INVALID) & (rel > 2.0) & (rel < 32)
        heights = rel[canopy]
        if len(heights) < 24 or canopy.mean() < 0.11:
            continue
        lower, upper, top = np.percentile(heights, [20, 80, 95])
        if (
            upper - lower < 0.9
            or multireturn[indices[canopy]].mean() < 0.19
            or greener[indices[canopy]].mean() < 0.10
        ):
            continue
        height_list[i] = round(float(top), 2)


def rail_profiles(x, y, z, dem, dem_grid):
    routes = json.loads((ASSETS / "rail-tracks.json").read_text())
    west, south = -6400, -35700
    binsize = 5
    nx, ny = 80, 60
    bx = np.clip(((x - west) / binsize).astype(np.int32), 0, nx - 1)
    by = np.clip(((y - south) / binsize).astype(np.int32), 0, ny - 1)
    bins = by * nx + bx
    order = np.argsort(bins, kind="stable")
    boundaries = np.searchsorted(bins[order], np.arange(nx * ny + 1))

    def support(east, north, ux, uy):
        px, py = to_plane.transform(
            ORIGIN[0] + east / 90528, ORIGIN[1] + north / 110953
        )
        cx, cy = int((px - west) // binsize), int((py - south) // binsize)
        if not (1 <= cx < nx - 1 and 1 <= cy < ny - 1):
            return None
        keys = [
            r * nx + c for r in range(cy - 1, cy + 2) for c in range(cx - 1, cx + 2)
        ]
        subset = np.concatenate(
            [order[boundaries[k] : boundaries[k + 1]] for k in keys]
        )
        dx, dy = x[subset] - px, y[subset] - py
        along, across = dx * ux + dy * uy, dy * ux - dx * uy
        local = (np.abs(along) < 2.05) & (np.abs(across) < 1.05)
        height = z[subset[local]]
        cross = across[local]
        if len(height) < 28:
            return None
        # Independent spot checks on these above-ground southern approaches put the
        # track bed between 7.6 and 9 m T.P.; overhead structures peak at 14–17 m.
        candidate = (height >= 7.0) & (height <= 10.1)
        if candidate.sum() < 22:
            return None
        target = height[candidate]
        counts, edges = np.histogram(target, bins=np.arange(6.95, 10.36, 0.2))
        mode = edges[np.argmax(counts)] + 0.1
        same = np.abs(height - mode) < 0.20
        if same.mean() < 0.38 or same.sum() < 23 or np.ptp(cross[same]) < 1.1:
            return None
        measured = float(np.median(height[same]))
        col = int((px - dem_grid.c) / dem_grid.a)
        row = int((py - dem_grid.f) / dem_grid.e)
        ground = dem[row, col]
        if (
            ground == INVALID
            or not 1.8 < measured - ground < 9.6
            or np.std(height[same]) > 0.125
        ):
            return None
        return [round(east, 3), round(north, 3), round(measured, 2)]

    tracks = []
    for track in routes["tracks"]:
        if track["grade"] <= 0 or track["section"] not in ("track", "siding"):
            continue
        coords = np.asarray(track["points"], dtype=float)
        segments = np.diff(coords, axis=0)
        lengths = np.linalg.norm(segments, axis=1)
        cumulative = np.concatenate(([0.0], np.cumsum(lengths)))
        stations = np.linspace(0, cumulative[-1], math.ceil(cumulative[-1] / 4) + 1)
        chain = []
        chains = []
        for station in stations:
            index = min(
                len(lengths) - 1, np.searchsorted(cumulative, station, side="right") - 1
            )
            if lengths[index] < 1e-6:
                continue
            ux, uy = segments[index] / lengths[index]
            east, north = coords[index] + (station - cumulative[index]) * np.array(
                [ux, uy]
            )
            observation = support(east, north, ux, uy)
            if observation is None or (
                chain and abs(chain[-1][2] - observation[2]) > 0.31
            ):
                if len(chain) >= 4:
                    chains.append(chain)
                chain = []
            if observation:
                chain.append(observation)
        if len(chain) >= 4:
            chains.append(chain)
        for part, chain in enumerate(chains):
            tracks.append(
                {
                    "id": track["id"],
                    "part": part,
                    "route": track["route"],
                    "gauge": track["gauge"],
                    "points": chain,
                }
            )
    file = "survey-rail.json"
    total = sum(len(t["points"]) for t in tracks)
    output = {
        "source": "東京都区部航空レーザLAS 09LD1884、© OpenStreetMap contributors の線形から加工",
        "sourceLAS": f"{DIG}/lp/09LD1884.zip",
        "sourceCrs": "EPSG:6677",
        "sourceOSM": routes["source"],
        "osmLicense": routes["license"],
        "metroLicense": "CC BY 4.0",
        "coordinates": "local east, north, observed support elevation above sea level in metres",
        "method": "approximately 4 m centres, a 4.1 × 2.1 m oriented window; median of the locally dominant lower return stratum with lateral support and continuity; roofs and gaps rejected; not a railhead measurement",
        "tracks": tracks,
    }
    (ASSETS / file).write_text(
        json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    return {
        "file": file,
        "source": "metro-las",
        "sheet": "09LD1884",
        "segmentCount": len(tracks),
        "sampleCount": total,
        "unit": "m",
        "vertical": "observed lower support surface elevation (Japanese national height datum)",
    }


def lidar_products(files):
    positions = json.loads((ROOT / "tools/tree-layout.json").read_text())
    tree_heights = [None] * len(positions)
    surfaces = []
    for sheet in LAS_SHEETS:
        with (
            zipfile.ZipFile(files["lp", sheet]) as archive,
            archive.open(sheet + ".las") as raw,
        ):
            points = laspy.read(raw)
        x, y, z = np.asarray(points.x), np.asarray(points.y), np.asarray(points.z)
        surfaces.append(point_surface(x, y, z, sheet))
        surfaces[-1]["pointCount"] = len(z)
        surfaces[-1]["returnsPerSquareMetre"] = round(len(z) / 120000, 2)
        dem, grid, _ = read_tiff(files["grd25", sheet])
        if sheet == "09LD1884":
            rail = rail_profiles(x, y, z, dem[0], grid)
        if sheet == "09LD1873":
            canopy_heights(positions, tree_heights, points, x, y, z, dem[0], grid)
    file = "survey-trees.json"
    (ASSETS / file).write_text(
        json.dumps({"heights": tree_heights}, separators=(",", ":")) + "\n"
    )
    record = {
        "file": file,
        "source": "metro-las",
        "unit": "m",
        "layout": "tools/tree-layout.json",
        "sampleCount": len(tree_heights),
        "measuredCount": sum(h is not None for h in tree_heights),
        "method": "p95 above the sampled DEM within a crown disk on the existing park tree site, vegetation colour and multiple-return filters; null for insufficient evidence",
    }
    return surfaces, record, rail


def main():
    jobs = [(layer, code) for layer in ("grd25", "ortho") for code in SHEETS] + [
        ("lp", code) for code in LAS_SHEETS
    ]
    with ThreadPoolExecutor(6) as pool:
        fetched = list(pool.map(get_sheet, jobs))
    files = {(layer, code): path for layer, code, path, _ in fetched}
    print("Fetched", len(files), "Tokyo sheets", flush=True)
    photo_base = gsi_mosaic("seamlessphoto", 18, True)
    dem_base = gsi_mosaic("dem5a_png", 15, False)
    photos = [prefecture_raster("ortho", files, photo_base), photo_base]
    elevations = [prefecture_raster("grd25", files, dem_base), dem_base]
    print("Built nested ortho and ground rasters", flush=True)
    surfaces, trees, rail = lidar_products(files)
    source_records = [
        {
            "id": "metro-" + label,
            "provider": "東京都",
            "dataset": "東京都デジタルツイン実現プロジェクト 区部点群データ",
            "product": product,
            "catalog": METRO_PAGE,
            "distribution": GIC_PAGE,
            "sourceCrs": "EPSG:6677",
            "license": "CC BY 4.0",
            "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
            "distributionTerms": "https://gic-tokyo.s3.ap-northeast-1.amazonaws.com/2023/dig/doc/license.pdf",
            "index": f"{DIG}/Vectortile/23ku/{layer}/{{z}}/{{x}}/{{y}}.pbf",
        }
        for label, layer, product in [
            ("ortho", "ortho", "簡易オルソ写真 0.20 m"),
            ("grd25", "grd25", "グリッドDEM 0.25 m"),
            ("las", "lp", "航空レーザ LAS（DSM及びグラウンドデータ）"),
        ]
    ]
    for label, layer, ext in (
        ("photo", "seamlessphoto", "jpg"),
        ("dem5a", "dem5a_png", "png"),
    ):
        source_records.append(
            {
                "id": "gsi-" + label,
                "provider": "国土地理院",
                "product": layer,
                "url": f"{GSI}/{layer}/{{z}}/{{x}}/{{y}}.{ext}",
                "catalog": "https://maps.gsi.go.jp/development/ichiran.html",
                "terms": "https://www.gsi.go.jp/kikakuchousei/kikakuchousei40182.html",
            }
        )
    result = {
        "version": 1,
        "origin": ORIGIN,
        "photos": photos,
        "elevations": elevations,
        "trees": trees,
        "surfaces": surfaces,
        "rail": rail,
        "sources": source_records,
        "inputArchives": [r for _, _, _, r in fetched],
    }
    for r in [*photos, *elevations, *surfaces, trees, rail]:
        r.update(file_proof(ASSETS / r["file"]))
    (ASSETS / "survey.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print("Measured tree crowns:", trees["measuredCount"], flush=True)


if __name__ == "__main__":
    main()
