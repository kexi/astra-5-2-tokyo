# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Follow official CityGML polygon targets for untextured buildings and selected IDs."""

import argparse
import csv
import gzip
import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
GML = "{http://www.opengis.net/gml}"
BUILDING = "{http://www.opengis.net/citygml/building/2.0}"
APPEARANCE = "{http://www.opengis.net/citygml/appearance/2.0}"


def inspect(stream, filename, wanted):
    owners, images = {}, {}
    for _, element in ET.iterparse(stream, events=("end",)):
        if element.tag == BUILDING + "Building":
            gml_id = element.get(GML + "id")
            identifier = wanted.get(gml_id)
            if identifier:
                boundaries = {
                    kind: {
                        polygon.get(GML + "id")
                        for surface in element.iter(BUILDING + kind)
                        for polygon in surface.iter(GML + "Polygon")
                    }
                    for kind in ("WallSurface", "RoofSurface")
                }
                boundaries["all"] = {
                    polygon.get(GML + "id") for polygon in element.iter(GML + "Polygon")
                }
                lod = sorted(
                    {
                        node.tag[len(BUILDING) :]
                        for node in element.iter()
                        if node.tag.startswith(BUILDING + "lod")
                    }
                )
                owners[identifier] = gml_id, boundaries, lod
            element.clear()
        elif element.tag == APPEARANCE + "ParameterizedTexture":
            uri = element.findtext(APPEARANCE + "imageURI")
            for target in element.findall(APPEARANCE + "target"):
                if uri:
                    images.setdefault(target.attrib["uri"].lstrip("#"), set()).add(uri)
            element.clear()
    return {
        identifier: {
            "gml": gml_id,
            "file": filename,
            "geometryProperties": lod,
            "surfaces": {
                key: {
                    "polygons": len(polygons),
                    "mapped": sum(p in images for p in polygons),
                    "images": sorted(
                        {uri for p in polygons for uri in images.get(p, ())}
                    ),
                }
                for key, polygons in boundaries.items()
            },
        }
        for identifier, (gml_id, boundaries, lod) in owners.items()
    }


def collect(path, meshes, wanted):
    if path.suffix == ".zip":
        with ZipFile(path) as archive:
            for name in archive.namelist():
                filename = Path(name).name
                if (
                    filename.endswith(".gml")
                    and filename.split("_")[0] in meshes
                    and "/bldg/" in name
                ):
                    with archive.open(name) as stream:
                        yield from inspect(stream, filename, wanted).items()
        return
    compressed = path.name.endswith(".gml.gz")
    opener = gzip.open if compressed else open
    with opener(path, "rb") as stream:
        yield from inspect(stream, path.name.removesuffix(".gz"), wanted).items()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sources",
        type=Path,
        nargs="+",
        help="Extracted GML, .gml.gz or official CityGML ZIP files",
    )
    parser.add_argument(
        "--id",
        action="append",
        default=[],
        help="Additional official building ID to inspect",
    )
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    with (ROOT / "review/building-textures.csv").open() as stream:
        selected = [
            r
            for r in csv.DictReader(stream)
            if r["coverage"] == "none" or r["building_id"] in args.id
        ]
    wanted = {r["gml_id"]: r["building_id"] for r in selected}
    missing_ids = set(args.id).difference(wanted.values())
    if missing_ids:
        parser.error(f"Unknown building IDs: {sorted(missing_ids)}")
    meshes = {r["meshcode"] for r in selected}
    records = {}
    for path in args.sources:
        for identifier, report in collect(path, meshes, wanted):
            if identifier in records and records[identifier] != report:
                raise ValueError(f"Conflicting CityGML entries for {identifier}")
            records[identifier] = report
    missing = set(wanted.values()).difference(records)
    if missing:
        raise ValueError(
            f"Missing {len(missing)} requested records, including {min(missing)}"
        )
    stats = Counter()
    for row in selected:
        if row["coverage"] == "none":
            stats["originallyWithoutPhotos"] += 1
            record = records[row["building_id"]]
            stats["withRawTextureTargets"] += bool(record["surfaces"]["all"]["mapped"])
    args.report.write_text(
        json.dumps(
            {"counts": dict(stats), "buildings": records},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    print(json.dumps(dict(stats)))


if __name__ == "__main__":
    main()
