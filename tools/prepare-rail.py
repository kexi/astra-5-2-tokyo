# /// script
# requires-python = ">=3.11"
# ///
"""Extract surveyed railway references and OSM track centerlines around Tokyo Station."""

import argparse
import json
import math
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(tempfile.gettempdir()) / "tokyo-open-data"
ORIGIN = (139.76610, 35.68117)
OSM_BOUNDS = (139.7575, 35.6735, 139.777, 35.6915)
OSM = "https://api.openstreetmap.org/api/0.6/map?bbox=" + ",".join(map(str, OSM_BOUNDS))
N02 = "https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-25/N02-25_GML.zip"
GSI = "https://cyberjapandata.gsi.go.jp/xyz/experimental_railcl/16/"
GEOID = "https://vldb.gsi.go.jp/sokuchi/surveycalc/geoid/calcgh/cgi/geoidcalc.pl"
DISPLAY_BOUNDS = (-500, -760, 720, 920)


def download(url, filename):
    target = CACHE / filename
    if target.exists():
        return target
    request = Request(
        url, headers={"User-Agent": "TokyoStationOpenData/1 (model data import)"}
    )
    with urlopen(request, timeout=45) as response:
        target.write_bytes(response.read())
    return target


def project(lon, lat):
    return (round((lon - ORIGIN[0]) * 90528, 3), round((lat - ORIGIN[1]) * 110953, 3))


def in_bounds(point):
    left, bottom, right, top = DISPLAY_BOUNDS
    x, y = point
    return left <= x <= right and bottom <= y <= top


def sections(points):
    """Split at missing or out-of-bounds nodes in the provider's extracts."""
    current = []
    for point in [*points, None]:
        if point is not None and in_bounds(point):
            if not current or current[-1] != point:
                current.append(point)
            continue
        if len(current) > 1:
            yield current
        current = []


def write(name, content):
    (ROOT / "assets" / name).write_text(
        json.dumps(content, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def osm_tracks(path):
    xml = ET.parse(path)
    nodes = {
        node.attrib["id"]: project(float(node.attrib["lon"]), float(node.attrib["lat"]))
        for node in xml.iter("node")
    }
    results = []
    for way in xml.iter("way"):
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.iter("tag")}
        is_rail = tags.get("railway") == "rail"
        layer = tags.get("layer", "0")
        is_surface = not (
            tags.get("tunnel")
            or layer.startswith("-")
            or tags.get("location") == "underground"
        )
        if not (is_rail and is_surface):
            continue
        gauge = tags.get("gauge", "").split(";")[0]
        points = [nodes.get(node.attrib["ref"]) for node in way.iter("nd")]
        for number, part in enumerate(sections(points), 1):
            results.append(
                {
                    "id": way.attrib["id"] + "." + str(number),
                    "route": tags.get("name"),
                    "grade": int(layer) if layer.isdigit() else 0,
                    "gauge": int(gauge) if gauge.isdigit() else 1067,
                    "section": tags.get("bridge", tags.get("service", "track")),
                    "points": part,
                }
            )
    return results


def rail_tiles():
    def slippy(lon, lat):
        return (
            int((lon + 180) / 360 * 65536),
            int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) * 32768),
        )

    longitude, latitude = ORIGIN
    x0, y0 = slippy(longitude - 0.009, latitude + 0.010)
    x1, y1 = slippy(longitude + 0.011, latitude - 0.009)
    urls = []
    references = []
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            url = f"{GSI}{x}/{y}.geojson"
            urls.append(url)
            tile = json.loads(download(url, f"rail-gsi-{x}-{y}.geojson").read_text())
            for feature in tile["features"]:
                properties = feature["properties"]
                if properties.get("railState") == "地下":
                    continue
                for part in sections(
                    [
                        project(*point[:2])
                        for point in feature["geometry"]["coordinates"]
                    ]
                ):
                    references.append(
                        {
                            "id": properties["rID"],
                            "grade": properties["railState"],
                            "route": properties.get("rtCode"),
                            "points": part,
                        }
                    )
    return urls, references


def national_lines(path):
    results = []
    with ZipFile(path) as archive:
        files = [
            name for name in archive.namelist() if name.lower().endswith(".geojson")
        ]
        for filename in files:
            for feature in json.loads(archive.read(filename))["features"]:
                geometry, properties = feature["geometry"], feature["properties"]
                points = geometry["coordinates"]
                paths = [points] if geometry["type"] == "LineString" else points
                for line in paths:
                    has_nearby_node = any(
                        abs(point[0] - ORIGIN[0]) < 0.012
                        and abs(point[1] - ORIGIN[1]) < 0.011
                        for point in line
                    )
                    if not has_nearby_node:
                        continue
                    for part in sections([project(*point[:2]) for point in line]):
                        results.append(
                            {
                                "route": properties.get("N02_003"),
                                "operator": properties.get("N02_004"),
                                "station": properties.get("N02_005"),
                                "points": part,
                            }
                        )
    if not results:
        raise ValueError("N02 did not contain railway reference lines in the AOI")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--osm", type=Path, help="Reuse an existing bbox .osm XML snapshot"
    )
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    osm_path = args.osm or download(OSM, "osm-rail-area.osm")
    tracks = osm_tracks(osm_path)
    if not tracks:
        raise ValueError("No surface tracks found in the OpenStreetMap extract")
    write(
        "rail-tracks.json",
        {
            "source": OSM,
            "attribution": "© OpenStreetMap contributors",
            "license": "https://opendatacommons.org/licenses/odbl/1-0/",
            "origin": ORIGIN,
            "coordinateSpace": "local east/north in metres",
            "tracks": tracks,
        },
    )
    urls, gsi = rail_tiles()
    national = national_lines(download(N02, "n02.zip"))
    url = f"{GEOID}?latitude={ORIGIN[1]}&longitude={ORIGIN[0]}&outputType=json"
    geoid_response = json.loads(download(url, "geoid-origin.json").read_text())
    geoid = float(geoid_response["OutputData"]["geoidHeight"])
    write(
        "rail-reference.json",
        {
            "gsi": {"sources": urls, "features": gsi},
            "n02": {"source": N02, "license": "CC BY 4.0", "features": national},
            "verticalDatum": {"name": "JPGEO2024", "source": url, "geoidHeight": geoid},
        },
    )
    print(
        f"Rail assets: {len(tracks)} OSM tracks / {len(gsi)} GSI references / {len(national)} N02 references, geoid {geoid} m"
    )


if __name__ == "__main__":
    main()
