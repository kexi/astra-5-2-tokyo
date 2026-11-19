# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Freeze the citywide screening decisions and their separate uncertainty register."""

import argparse
import csv
import gzip
import json
from pathlib import Path


def rectangle(box):
    l, t, r, b = box
    return [(l, t), (r, t), (r, b), (l, b)]


def structural(side, box, donor, patch, score):
    if donor != side or score < 0.98:
        return False
    left, top, right, bottom = box
    area = (right - left) * (bottom - top)
    if not 0.075 <= area <= 0.73:
        return False
    # Require a clean strip from this very elevation. Disjoint crops are
    # evidence of a local change rather than a braced facade over its full height.
    a, b, c, d = patch
    return (
        d < top + 0.05 or b > bottom - 0.05 or c < left + 0.05 or a > right - 0.05
    ) and (c - a) * (d - b) > 0.011


def photographic_evidence(texture):
    if texture is None:
        return False
    contrast, oblique, dominant_orientation, repetition, interior = texture
    return (
        contrast >= 4
        and oblique >= 30
        and dominant_orientation <= 63
        and repetition < 79
        and interior >= 78
    )


def obstructed_regions(horizons, box, detail=None, min_angle=40):
    # All three possible look directions must meet a different measured roof
    # above 40 degrees. Apply only the affected subregion of the photograph.
    left, top, right, bottom = box
    runs = []
    for y in range(8):
        line = [
            x
            for x in range(8)
            if left <= (x + 0.5) / 8 <= right
            and top <= (y + 0.5) / 8 <= bottom
            and horizons[8 * y + x] >= min_angle
            and (detail is None or photographic_evidence(detail[8 * y + x]))
        ]
        while line:
            first = last = line.pop(0)
            while line and line[0] == last + 1:
                last = line.pop(0)
            key = (first, last)
            existing = next((r for r in runs if r[:2] == [*key] and r[3] == y), None)
            if existing:
                existing[3] = y + 1
            else:
                runs.append([first, last, y, y + 1])
    if sum((b - a + 1) * (d - c) for a, b, c, d in runs) < 3:
        return []
    return [
        rectangle(
            (
                max(left, a / 8),
                max(top, c / 8),
                min(right, (b + 1) / 8),
                min(bottom, d / 8),
            )
        )
        for a, b, c, d in runs
    ]


def decide(proposals, geographic=None, horizons=None, details=None):
    automatic = {}
    unresolved = {}
    for identifier, items in proposals.items():
        plans = []
        doubtful = []
        for side, box, donor, patch, score in items:
            same_wall = structural(side, box, donor, patch, score)
            angles = (horizons or {}).get(identifier, {}).get(str(side))
            if details is None:
                masks = (
                    [rectangle(box)]
                    if same_wall
                    else obstructed_regions(angles, box)
                    if angles is not None
                    else []
                )
            else:
                # The original photo must also be inconsistent with a flat finish,
                # an orthogonal window grid or regularly spaced diagonal bracing.
                detail = details.get(identifier, [None] * 4)[side]
                if not detail:
                    masks = []
                elif same_wall:
                    masks = obstructed_regions([90] * 64, box, detail)
                elif angles is not None and score >= 0.92:
                    masks = obstructed_regions(angles, box, detail, 50)
                else:
                    masks = []
            reason = 8 if same_wall else 10 if donor is not None else 11
            plans.extend([side, mask, donor, patch, reason] for mask in masks)
            # The remaining area of this proposal retains the original image.
            doubtful.append([side, [round(p, 3) for p in box], score])
        for direction, matches in (geographic or {}).get(identifier, {}).items():
            side = int(direction)
            nearby = next((r for r in items if r[0] == side and r[2] is not None), None)
            for match in matches:
                if nearby:
                    plans.append([side, match["contour"], nearby[2], nearby[3], 7])
                else:
                    plans.append([side, match["contour"], None, None, 9])
        if plans:
            automatic[identifier] = plans
        if doubtful:
            unresolved[identifier] = doubtful
    return automatic, unresolved


def read_json(path):
    with gzip.open(path, "rt") as f:
        return json.load(f)


def adjudicated(accepted, uncertain):
    directory = Path(__file__).resolve().parent
    retained = {
        record["id"]: "existing-landmark-paint"
        for record in json.loads((directory / "omy-buildings.json").read_text())
    }
    for name in ("building-paint-sample.csv", "building-paint-holdouts.csv"):
        with (directory.parent / "review" / name).open() as stream:
            for row in csv.DictReader(stream):
                if row["finding"] in {"not_seen", "uncertain"}:
                    retained[row["id"]] = row["finding"]
    # The photo belongs to the building, but these particular proposed regions
    # have insufficient evidence for a replacement following paired review.
    for identifier in (
        "13103-bldg-1617",
        "13101-bldg-3258",
        "13102-bldg-8496",
        "13102-bldg-10156",
        "13101-bldg-55",
        "13103-bldg-11589",
        "13102-bldg-15295",
    ):
        retained[identifier] = "uncertain"
    for identifier, status in retained.items():
        accepted.pop(identifier, None)
        if status == "not_seen":
            uncertain.pop(identifier, None)
    return retained


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("screen", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--geographic", type=Path)
    p.add_argument("--blind", type=Path)
    p.add_argument("--horizons", type=Path)
    p.add_argument("--details", type=Path)
    args = p.parse_args()
    accepted, uncertain = decide(
        read_json(args.screen),
        read_json(args.geographic) if args.geographic else None,
        read_json(args.horizons) if args.horizons else None,
        read_json(args.details) if args.details else None,
    )
    if args.blind:
        with args.blind.open() as f:
            ids = [r["id"] for r in csv.DictReader(f)]
        result = {
            i: {"apply": accepted.get(i, []), "uncertain": uncertain.get(i, [])}
            for i in sorted(ids)
        }
    else:
        retained = adjudicated(accepted, uncertain)
        result = {
            "version": 1,
            "plans": accepted,
            "uncertain": uncertain,
            "retained": retained,
        }
    args.output.write_bytes(
        gzip.compress(
            json.dumps(
                result, separators=(",", ":"), allow_nan=False, sort_keys=True
            ).encode(),
            mtime=0,
        )
    )
    print(json.dumps({"apply": len(accepted), "uncertain": len(uncertain)}))


if __name__ == "__main__":
    main()
