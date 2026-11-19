"""Per-building elevation masks verified against the eight-view first draft."""

import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
from blender_texture_charts import pack, photograph_charts
from blender_texture_lowrise import LOWRISE_MASKS
from blender_texture_towers import TOWER_MASKS
from omy_facades import elevation, inside_polygon, sample_donor


def rect(left, top, right, bottom):
    return ((left, top), (right, top), (right, bottom), (left, bottom))


FULL = rect(0, 0, 1, 1)
# Directions are north, east, south and west relative to a building's measured
# principal axes. Height runs downward in a rectified elevation. A negative
# donor index additionally includes steeply sloping surfaces on that side.
PLANS = {
    "13101-bldg-1643": [
        (0, FULL, 3, (0.08, 0.24, 0.84, 0.49)),
        (3, rect(0, 0.55, 1, 1), 3, (0.08, 0.24, 0.84, 0.49)),
    ],
    "13101-bldg-422": [
        (0, FULL, 3, (0.10, 0.25, 0.89, 0.89)),
        (1, rect(0, 0.37, 0.66, 1), 3, (0.10, 0.25, 0.89, 0.89)),
    ],
    "13101-bldg-2612": [
        (2, FULL, 0, (0.13, 0.22, 0.81, 0.81)),
        (3, FULL, 0, (0.13, 0.22, 0.81, 0.81)),
    ],
    "13101-bldg-1800": [
        (0, FULL, 1, (0.15, 0.33, 0.78, 0.72)),
        (3, FULL, 1, (0.15, 0.33, 0.78, 0.72)),
    ],
    "13101-bldg-4350": [
        (0, FULL, None, None),
        (1, FULL, None, None),
        (2, rect(0, 0, 0.71, 0.87), None, None),
        (3, rect(0, 0, 1, 0.83), None, None),
    ],
    "13101-bldg-1069": [
        (
            0,
            (
                (0.28, 0.38),
                (0.88, 0.38),
                (0.88, 0.48),
                (0.47, 0.79),
                (0.39, 0.84),
                (0.39, 0.44),
                (0.28, 0.44),
            ),
            0,
            (0.10, 0.20, 0.39, 0.70),
        ),
    ],
    "13101-bldg-10855": [
        (0, ((0, 0.11), (0.38, 0.11), (0.36, 1), (0, 1)), 0, (0.46, 0.18, 0.88, 0.78)),
        (2, rect(0.06, 0.02, 0.90, 0.23), 3, (0.09, 0.30, 0.70, 0.75)),
        (2, rect(0.06, 0.42, 0.86, 1), 3, (0.09, 0.30, 0.70, 0.75)),
    ],
    "13102-bldg-2335": [
        (0, rect(0.14, 0.46, 0.61, 1), 0, (0.25, 0.11, 0.89, 0.41)),
        (0, rect(0.57, 0.82, 1, 1), 0, (0.25, 0.11, 0.89, 0.41)),
        (1, rect(0, 0.83, 1, 1), 1, (0.07, 0.16, 0.86, 0.71)),
        (2, rect(0, 0.80, 0.87, 1), 2, (0.08, 0.12, 0.72, 0.74)),
    ],
    "13102-bldg-7361": [
        (0, rect(0, 0.55, 0.83, 1), 0, (0.10, 0.23, 0.78, 0.52)),
        (1, rect(0.10, 0.35, 0.94, 1), 2, (0.11, 0.23, 0.91, 0.63)),
        (2, rect(0, 0.71, 1, 1), 2, (0.11, 0.23, 0.91, 0.63)),
        (3, ((0, 0.66), (0.36, 1), (0, 1)), 3, (0.22, 0.25, 0.78, 0.64)),
    ],
    "13103-bldg-2213": [
        (
            0,
            ((0.33, 1), (0.70, 0.10), (0.81, 0.10), (0.81, 0.38), (1, 0.37), (1, 1)),
            0,
            (0.08, 0.19, 0.42, 0.78),
        ),
        (
            2,
            ((0, 0.11), (0.28, 0.1), (0.02, 0.67), (0, 0.67)),
            0,
            (0.08, 0.19, 0.42, 0.78),
        ),
        (2, rect(0.75, 0.08, 1, 1), 0, (0.08, 0.19, 0.42, 0.78)),
        (3, ((0, 0.67), (0.50, 0.9), (0.50, 1), (0, 1)), 3, (0.56, 0.24, 0.89, 0.68)),
    ],
    "13103-bldg-3033": [
        (0, rect(0, 0, 0.64, 1), 2, (0.08, 0.28, 0.76, 0.75)),
        (1, rect(0, 0.43, 1, 1), 1, (0.09, 0.22, 0.40, 0.41)),
        (1, rect(0.47, 0.11, 0.94, 0.43), 1, (0.09, 0.22, 0.40, 0.41)),
        (3, rect(0, 0.10, 0.78, 1), 2, (0.08, 0.28, 0.76, 0.75)),
    ],
    "13103-bldg-10859": [
        (
            1,
            (
                (0, 0.49),
                (0.11, 0.49),
                (0.43, 0.63),
                (0.34, 0.73),
                (0.75, 0.84),
                (0.79, 1),
                (0, 1),
            ),
            1,
            (0.14, 0.15, 0.76, 0.45),
        ),
    ],
    "13101-bldg-2089": [
        (0, rect(0, 0.83, 0.91, 1), 0, (0.21, 0.58, 0.80, 0.80)),
        (1, rect(0.25, 0.86, 0.98, 1), 2, (0.13, 0.83, 0.91, 0.93)),
        (3, rect(0.03, 0.01, 0.65, 0.24), 0, (0.21, 0.12, 0.80, 0.72)),
        (
            3,
            ((0.16, 0.20), (0.64, 0.20), (0.64, 0.75), (0.19, 0.53)),
            0,
            (0.21, 0.12, 0.80, 0.72),
        ),
    ],
    "13101-bldg-3560": [
        (
            0,
            ((0.87, 0.51), (1, 0.50), (1, 0.79), (0.38, 0.89), (0.12, 0.86)),
            0,
            (0.15, 0.14, 0.86, 0.47),
        ),
        (2, rect(0, 0.35, 0.78, 0.97), 2, (0.11, 0.15, 0.86, 0.32)),
    ],
    "13101-bldg-3969": [
        (0, rect(0, 0.62, 0.94, 1), 0, (0.12, 0.22, 0.82, 0.61)),
        (1, rect(0, 0.42, 0.66, 1), 1, (0.06, 0.14, 0.25, 0.39)),
        (2, rect(0.26, 0.26, 0.97, 0.99), 2, (0.04, 0.12, 0.31, 0.62)),
        (3, rect(0, 0.57, 0.78, 1), 3, (0.09, 0.12, 0.85, 0.55)),
    ],
    "13101-bldg-2031": [
        (0, FULL, 3, (0.19, 0.24, 0.67, 0.49)),
        (1, FULL, 3, (0.19, 0.24, 0.67, 0.49)),
        (2, rect(0.32, 0.78, 1, 1), 2, (0.48, 0.23, 0.70, 0.67)),
    ],
    "13101-bldg-604": [
        (0, rect(0, 0.42, 0.88, 1), 2, (0.09, 0.44, 0.88, 0.83)),
        (0, rect(0, 0, 1, 0.24), 2, (0.09, 0.44, 0.88, 0.83)),
        (1, FULL, 3, (0.12, 0.46, 0.69, 0.83)),
        (2, rect(0, 0, 1, 0.23), 2, (0.09, 0.44, 0.88, 0.83)),
        (3, rect(0, 0, 1, 0.25), 3, (0.12, 0.46, 0.69, 0.83)),
    ],
    "13101-bldg-1866": [
        (1, FULL, 3, (0.09, 0.13, 0.91, 0.45)),
        (2, rect(0, 0.69, 1, 1), 2, (0.09, 0.13, 0.90, 0.58)),
        (3, rect(0, 0.48, 1, 1), 3, (0.09, 0.13, 0.91, 0.45)),
    ],
    "13101-bldg-4149": [
        (0, rect(0.21, 0.43, 0.52, 1), 0, (0.55, 0.23, 0.89, 0.82)),
        (2, rect(0.58, 0.27, 0.81, 0.92), 2, (0.23, 0.19, 0.40, 0.83)),
    ],
    "13101-bldg-1687": [(2, FULL, 0, (0.05, 0.20, 0.94, 0.93))],
    "13101-bldg-1771": [
        (0, rect(0, 0.12, 1, 1), -4, (0.10, 0.32, 0.88, 0.55)),
        (
            2,
            (
                (0, 0.57),
                (0.23, 0.45),
                (0.47, 0.49),
                (0.69, 0.39),
                (0.86, 0.41),
                (1, 0.59),
                (1, 1),
                (0, 1),
            ),
            -4,
            (0.1, 0.32, 0.88, 0.55),
        ),
        (3, rect(0, 0.60, 1, 1), -4, (0.1, 0.32, 0.88, 0.55)),
        (1, rect(0, 0.72, 1, 1), -4, (0.1, 0.32, 0.88, 0.55)),
    ],
    "13101-bldg-3449": [
        (1, rect(0.22, 0.07, 0.91, 0.82), 3, (0.12, 0.16, 0.79, 0.74)),
        (2, rect(0.48, 0.28, 0.98, 0.78), 0, (0.06, 0.16, 0.40, 0.68)),
        (0, rect(0.43, 0.84, 0.99, 1), 3, (0.10, 0.82, 0.29, 0.94)),
        (3, rect(0.25, 0.84, 0.93, 1), 3, (0.10, 0.82, 0.23, 0.95)),
    ],
    "13101-bldg-1824": [
        (0, rect(0.02, 0.26, 0.68, 0.99), 0, (0.05, 0.12, 0.24, 0.43)),
        (1, rect(0.05, 0.17, 0.71, 1), 1, (0.77, 0.10, 0.94, 0.72)),
        (2, rect(0, 0.61, 0.94, 1), 2, (0.36, 0.12, 0.93, 0.59)),
    ],
    "13101-bldg-1033": [
        (1, FULL, 0, (0.24, 0.08, 0.93, 0.40)),
        (2, rect(0, 0.01, 0.48, 0.99), 0, (0.24, 0.08, 0.93, 0.40)),
        (3, rect(0, 0.01, 0.32, 0.95), 0, (0.24, 0.08, 0.93, 0.40)),
        (3, rect(0.52, 0.48, 1, 1), 0, (0.24, 0.08, 0.93, 0.40)),
    ],
    "13101-bldg-1261": [
        (1, rect(0.08, 0.02, 1, 1), 0, (0.055, 0.14, 0.60, 0.69)),
        (2, rect(0.56, 0.02, 1, 1), 2, (0.19, 0.07, 0.48, 0.72)),
        (2, rect(0, 0.74, 0.58, 1), 2, (0.19, 0.07, 0.48, 0.72)),
        (3, rect(0, 0.34, 1, 1), 0, (0.055, 0.14, 0.60, 0.69)),
    ],
    "13101-bldg-2033": [
        (0, rect(0, 0, 0.62, 0.98), 0, (0.69, 0.24, 0.86, 0.63)),
        (1, rect(0.51, 0.02, 1, 0.99), 1, (0.10, 0.25, 0.45, 0.69)),
        (2, rect(0, 0, 0.53, 1), 2, (0.60, 0.13, 0.93, 0.78)),
    ],
    "13101-bldg-3125": [
        (0, rect(0, 0.14, 1, 0.80), 2, (0.09, 0.17, 0.92, 0.70)),
        (3, rect(0, 0.11, 0.90, 1), 2, (0.09, 0.17, 0.92, 0.70)),
    ],
    "13101-bldg-1368": [
        (0, rect(0.03, 0.57, 0.70, 1), 0, (0.12, 0.16, 0.87, 0.50)),
        (1, rect(0, 0.15, 0.66, 0.90), 1, (0.70, 0.23, 0.89, 0.77)),
        (2, rect(0, 0.10, 0.99, 0.46), 2, (0.08, 0.51, 0.92, 0.89)),
        (3, rect(0, 0.16, 1, 0.66), 2, (0.08, 0.51, 0.92, 0.89)),
    ],
    "13101-bldg-1538": [
        (0, rect(0, 0.10, 1, 0.51), 3, (0.12, 0.54, 0.50, 0.88)),
        (1, rect(0, 0.10, 1, 0.71), 3, (0.12, 0.54, 0.50, 0.88)),
        (2, rect(0, 0.00, 1, 0.52), 3, (0.12, 0.54, 0.50, 0.88)),
        (3, rect(0, 0.02, 1, 0.54), 3, (0.12, 0.54, 0.50, 0.88)),
        (
            3,
            ((0.44, 0.49), (0.51, 0.49), (1, 0.88), (1, 0.94)),
            3,
            (0.12, 0.55, 0.43, 0.86),
        ),
    ],
    "13101-bldg-157": [
        (0, FULL, 2, (0.10, 0.15, 0.90, 0.87)),
        (
            1,
            ((0.53, 0), (1, 0), (1, 1), (0.84, 1), (0.84, 0.40)),
            3,
            (0.48, 0.14, 0.89, 0.80),
        ),
        (3, rect(0, 0.01, 0.41, 1), 3, (0.48, 0.14, 0.89, 0.80)),
    ],
    "13101-bldg-514": [
        (0, rect(0.07, 0, 0.92, 0.67), 1, (0.42, 0.12, 0.90, 0.81)),
        (3, rect(0, 0.35, 0.49, 1), 3, (0.06, 0.10, 0.42, 0.32)),
        (3, rect(0.49, 0.48, 0.99, 1), 3, (0.06, 0.10, 0.42, 0.32)),
    ],
    "13101-bldg-703": [
        (0, rect(0, 0.31, 0.80, 1), 0, (0.35, 0.14, 0.91, 0.31)),
        (1, rect(0.24, 0, 1, 1), 1, (0.035, 0.20, 0.23, 0.68)),
        (2, FULL, 0, (0.35, 0.14, 0.91, 0.31)),
        (3, rect(0, 0, 0.56, 1), 0, (0.35, 0.14, 0.91, 0.31)),
    ],
    "13101-bldg-1343": [
        (0, rect(0.19, 0.20, 0.85, 0.99), 0, (0.29, 0.06, 0.90, 0.19)),
    ],
    "13101-bldg-4002": [
        (0, rect(0, 0.62, 0.20, 1), 0, (0.10, 0.15, 0.43, 0.57)),
        (0, rect(0.48, 0.60, 1, 1), 0, (0.10, 0.15, 0.43, 0.57)),
        (3, rect(0, 0.38, 0.41, 0.96), 1, (0.21, 0.17, 0.86, 0.78)),
    ],
    "13101-bldg-3631": [
        (0, rect(0, 0.08, 0.62, 1), 2, (0.54, 0.14, 0.80, 0.63)),
        (1, rect(0.24, 0.12, 0.90, 0.96), 2, (0.54, 0.14, 0.80, 0.63)),
    ],
    "13101-bldg-3837": [
        (0, rect(0, 0.44, 0.68, 1), 0, (0.12, 0.10, 0.76, 0.38)),
        (1, rect(0, 0.19, 0.86, 0.91), 3, (0.42, 0.20, 0.90, 0.79)),
        (2, rect(0, 0.20, 1, 0.98), 3, (0.42, 0.20, 0.90, 0.79)),
    ],
}


for identifier, ((source, patch), directions) in (TOWER_MASKS | LOWRISE_MASKS).items():
    PLANS[identifier] = [
        (side, rect(*bounds), source, patch)
        for side, regions in directions.items()
        for bounds in regions
    ]


# Additional wall bearings follow these buildings' measured curved or
# chamfered footprints. Their roofs remain entirely outside these projections.
PLANS["13101-bldg-1510"] = [
    (1.7, rect(0, 0.19, 1, 1), 2, (0.06, 0.24, 0.34, 0.61)),
    (1.9, rect(0.35, 0.15, 1, 1), 2, (0.06, 0.24, 0.34, 0.61)),
    (2, rect(0.37, 0.16, 1, 1), 2, (0.06, 0.24, 0.34, 0.61)),
    (2.2, rect(0.54, 0.13, 1, 1), 2, (0.06, 0.24, 0.34, 0.61)),
    (2.2, rect(0, 0.65, 0.17, 1), 2, (0.06, 0.24, 0.34, 0.61)),
    (0.8, rect(0, 0.76, 1, 1), 2, (0.06, 0.24, 0.34, 0.61)),
]
PLANS["13101-bldg-1767"].extend(
    [
        (
            0.5,
            ((0, 0.27), (0.55, 0.27), (0.53, 0.55), (0, 0.71)),
            0.5,
            (0.18, 0.07, 0.80, 0.24),
        ),
        (0.5, ((0, 0.86), (1, 0.49), (1, 1), (0, 1)), 0.5, (0.18, 0.07, 0.80, 0.24)),
    ]
)
PLANS["13102-bldg-7342"] = [
    (side, poly, 3, (0.62, 0.36, 0.89, 0.66))
    for side, poly, _, _ in PLANS["13102-bldg-7342"]
]
PLANS["13102-bldg-5657"] = [
    (side, poly, 2, (0.12, 0.37, 0.36, 0.66))
    for side, poly, _, _ in PLANS["13102-bldg-5657"]
]
PLANS["13102-bldg-8739"] += [
    (0, rect(0, 0.13, 0.98, 1), 1, (0.18, 0.18, 0.79, 0.83)),
    (3, rect(0.08, 0.33, 0.97, 1), 1, (0.18, 0.18, 0.79, 0.83)),
]
PLANS["13103-bldg-3249"].append((1, rect(0, 0.10, 1, 1), 0, (0.11, 0.13, 0.86, 0.60)))
PLANS["13103-bldg-8582"].append(
    (3, rect(0.02, 0.06, 1, 1), 2, (0.15, 0.13, 0.62, 0.74))
)
# These masks follow the silhouettes of neighbouring buildings in the
# full-resolution elevations. Each donor window is on the named building.
PLANS.update(
    {
        "13101-bldg-1591": [
            (
                1,
                (
                    (0.52, 0.96),
                    (0.52, 0.66),
                    (0.60, 0.59),
                    (0.63, 0.47),
                    (1, 0.40),
                    (1, 0.96),
                ),
                1,
                # Eleven measured window bays (176 of 429 texels) let the
                # paint continue horizontally on the original floor itself.
                (0.04, 0.09, 0.04 + 176 / 429, 0.963),
            ),
            (
                2,
                (
                    (0, 0.58),
                    (0.46, 0.56),
                    (0.64, 0.53),
                    (1, 0.58),
                    (1, 0.96),
                    (0, 0.96),
                ),
                2,
                (0.005, 0.22, 0.995, 0.22 + 173 / 802),
            ),
            (
                3,
                ((0.80, 0.88), (1, 0.77), (1, 1), (0.80, 1)),
                3,
                (0.18, 0.26, 0.79, 0.66),
            ),
        ],
        "13102-bldg-3119": [
            (0, FULL, 2, (0.08, 0.24, 0.85, 0.54)),
            (1, FULL, 2, (0.08, 0.24, 0.85, 0.54)),
            (
                2,
                ((0.68, 0.65), (0.79, 0.56), (1, 0.54), (1, 1), (0.65, 1)),
                2,
                (0.08, 0.24, 0.85, 0.54),
            ),
            (2, rect(0, 0.90, 0.50, 1), 2, (0.08, 0.24, 0.85, 0.54)),
            (3, FULL, 2, (0.08, 0.24, 0.85, 0.54)),
        ],
        "13103-bldg-3933": [
            (
                1,
                (
                    (0, 0.35),
                    (0.29, 0.34),
                    (0.52, 0.37),
                    (0.65, 0.44),
                    (0.65, 0.86),
                    (1, 0.82),
                    (1, 1),
                    (0, 1),
                ),
                1,
                (0.015, 0.16, 0.985, 0.16 + 64 / 499),
            ),
            (
                2,
                ((0, 0.69), (0.51, 0.69), (1, 0.74), (1, 1), (0, 1)),
                2,
                (0.11, 0.13, 0.89, 0.46),
            ),
        ],
        "13103-bldg-5502": [
            (0, FULL, 2, (0.10, 0.18, 0.45, 0.76)),
            (
                1,
                ((0.41, 0.61), (0.78, 0.61), (0.84, 0.89), (1, 1), (0.42, 1)),
                1,
                (0.27, 0.24, 0.72, 0.56),
            ),
            (3, rect(0, 0.09, 0.28, 1), 3, (0.33, 0.24, 0.79, 0.76)),
        ],
        "13103-bldg-7676": [
            (
                0,
                (
                    (0.08, 0.77),
                    (0.20, 0.66),
                    (0.46, 0.51),
                    (1, 0.51),
                    (1, 1),
                    (0.08, 1),
                ),
                0,
                (0.17, 0.12, 0.84, 0.48),
            ),
            (0, rect(0, 0, 0.12, 1), 0, (0.17, 0.12, 0.84, 0.48)),
            (
                1,
                (
                    (0, 0),
                    (0.17, 0),
                    (0.19, 0.27),
                    (0.34, 0.25),
                    (0.52, 0.27),
                    (0.56, 0.48),
                    (0.58, 1),
                    (0, 1),
                ),
                1,
                (0.26, 0.055, 0.78, 0.26),
            ),
            (
                1,
                (
                    (0.48, 1),
                    (0.58, 0.65),
                    (0.77, 0.61),
                    (0.82, 0.28),
                    (1, 0.05),
                    (1, 1),
                ),
                1,
                (0.26, 0.055, 0.78, 0.26),
            ),
            (3, ((0, 0.62), (0.41, 1), (0, 1)), 3, (0.22, 0.15, 0.70, 0.53)),
        ],
    }
)
# Several corner towers have secondary facades oblique to the two principal
# axes. Fractional bearings select the real corner wall without filling gaps
# in the silhouette or touching the physically modelled setbacks.
for ident, additions in {
    "13101-bldg-157": [
        (3, rect(0.37, 0.84, 0.94, 1), 3, (0.49, 0.19, 0.90, 0.74)),
    ],
    "13101-bldg-1844": [
        (0.3, rect(0.48, 0, 1, 1), 3, (0.48, 0.43, 0.80, 0.68)),
        (3.25, FULL, 3, (0.48, 0.43, 0.80, 0.68)),
    ],
    "13101-bldg-2425": [
        # The white equipment photograph is on the same upright triangle
        # from z=-2.82 m to z=105.38 m as the windows above it.
        (3, rect(0, 0.82, 0.48, 1), 3, (0.035, 0.28, 0.42, 0.71)),
        (
            2.5,
            ((0.20, 0.19), (0.43, 0.13), (1, 0.19), (1, 1), (0.20, 0.90)),
            3,
            (0.08, 0.23, 0.34, 0.74),
        ),
    ],
    "13101-bldg-2760": [
        (0.3, rect(0, 0.17, 1, 1), 3, (0.17, 0.26, 0.63, 0.49)),
        (1, rect(0.72, 0.25, 1, 1), 3, (0.17, 0.26, 0.63, 0.49)),
        (2.25, rect(0, 0.08, 1, 1), 3, (0.17, 0.26, 0.68, 0.51)),
        (3, rect(0.70, 0.23, 0.95, 0.59), 3, (0.17, 0.26, 0.63, 0.49)),
    ],
    "13101-bldg-3543": [
        (3, rect(0.03, 0.64, 0.91, 1), 2, (0.20, 0.08, 0.87, 0.30)),
    ],
    "13102-bldg-10828": [
        (
            0,
            ((0.10, 0.46), (0.32, 0.31), (0.69, 0.28), (1, 0.28), (1, 0.47)),
            3,
            (0.15, 0.12, 0.73, 0.32),
        ),
    ],
    "13102-bldg-5657": [
        (2, rect(0, 0.81, 0.48, 1), 2, (0.12, 0.37, 0.36, 0.66)),
    ],
    "13103-bldg-2572": [
        (
            2.5,
            (
                (0, 0.74),
                (0.42, 0.69),
                (0.46, 0.53),
                (0.87, 0.51),
                (1, 0.59),
                (1, 1),
                (0, 1),
            ),
            2.5,
            (0.15, 0.21, 0.82, 0.45),
        ),
        (2.75, rect(0.03, 0.51, 0.97, 1), 2.5, (0.15, 0.21, 0.82, 0.45)),
    ],
    "13103-bldg-4507": [
        (
            0,
            ((0.43, 0.83), (0.78, 0.83), (1, 0.73), (1, 1), (0.41, 1)),
            3,
            (0.14, 0.13, 0.83, 0.66),
        ),
        (
            3,
            (
                (0, 0.70),
                (0.14, 0.69),
                (0.29, 0.79),
                (0.55, 0.71),
                (0.75, 0.80),
                (1, 0.81),
                (1, 1),
                (0, 1),
            ),
            3,
            (0.14, 0.13, 0.83, 0.66),
        ),
    ],
    "13103-bldg-9171": [
        (
            0,
            ((0.12, 1), (0.27, 0.68), (0.28, 0.44), (0.65, 0.32), (1, 0.32), (1, 1)),
            0,
            (0.11, 0.06, 0.73, 0.32),
        ),
    ],
    "13103-bldg-9100": [
        (
            0,
            (
                (0.07, 0.80),
                (0.22, 0.61),
                (0.42, 0.40),
                (0.75, 0.38),
                (1, 0.30),
                (1, 0.79),
                (0.37, 0.91),
                (0.08, 0.94),
            ),
            0,
            (0.15, 0.055, 0.72, 0.19),
        ),
    ],
}.items():
    PLANS.setdefault(ident, []).extend(additions)
MANUAL_IDS = frozenset(PLANS)
REGISTRY = Path(__file__).with_name("blender_texture_screening.json.gz")
if REGISTRY.exists():
    with gzip.open(REGISTRY, "rt") as stream:
        screening = json.load(stream)
    if screening["version"] != 1:
        raise ValueError("Unsupported facade screening registry")
    PLANS.update(
        {i: entries for i, entries in screening["plans"].items() if i not in MANUAL_IDS}
    )
    UNCERTAIN = screening["uncertain"]
    RETAINED = screening.get("retained", {})
    del screening
else:
    UNCERTAIN = {}
    RETAINED = {}


def mark_screen(obj):
    identifier = obj["id"]
    if not REGISTRY.exists():
        return
    obj["textureScreenState"] = (
        "astra-reviewed"
        if identifier in MANUAL_IDS
        else "locally-reconstructed"
        if obj.get("textureReviewFaces", 0)
        else "uncertain"
        if identifier in UNCERTAIN or identifier in PLANS
        else "screened"
        if obj.get("imageFaces")
        else "no-source-photograph"
    )
    if identifier in RETAINED and identifier not in MANUAL_IDS:
        verdict = RETAINED[identifier]
        obj["textureScreenState"] = (
            "uncertain"
            if verdict == "uncertain"
            else "astra-retained"
            if verdict == "not_seen"
            else "existing-landmark-paint"
        )
    if masks := UNCERTAIN.get(identifier):
        obj["textureUncertainRegions"] = json.dumps(masks, separators=(",", ":"))


def covered_patch(panel, patch):
    _, owners, _, _ = panel
    h, w = owners.shape
    left, top, right, bottom = patch
    a, b = max(0, int(left * w)), min(w, int(np.ceil(right * w)))
    y0, y1 = max(0, int(top * h)), min(h, int(np.ceil(bottom * h)))
    present = np.any(owners[y0:y1, a:b] >= 0, axis=1)
    if len(present) and present.all():
        return patch
    boundaries = np.flatnonzero(np.diff(np.r_[0, present, 0].astype(int))).reshape(
        -1, 2
    )
    if len(boundaries) == 0:
        raise ValueError(f"No photographed texels inside donor crop {patch}")
    start, end = boundaries[np.argmax(boundaries[:, 1] - boundaries[:, 0])] + y0
    return left, (start + 0.01) / h, right, (end - 0.01) / h


def sample_rgb(image, uv):
    size = np.array(image.shape[1::-1])
    p = np.clip(uv * size - 0.5, 0, size - 1)
    a = p.astype(int)
    b = np.minimum(a + 1, size - 1)
    x, y = (p - a).transpose(2, 0, 1)[..., None]
    lower = image[a[:, :, 1], a[:, :, 0]] * (1 - x) + image[a[:, :, 1], b[:, :, 0]] * x
    upper = image[b[:, :, 1], a[:, :, 0]] * (1 - x) + image[b[:, :, 1], b[:, :, 0]] * x
    return np.rint(lower * (1 - y) + upper * y).astype(np.uint8)


def face_chart(atlas, source_uv, face, panel, result, changed):
    _, owners, screen, _ = panel
    vertices = screen[face]
    low = np.floor(vertices.min(0) - 12).astype(int)
    high = np.ceil(vertices.max(0) + 12).astype(int)
    lo, bottom = low
    hi, top = high
    yy, xx = np.mgrid[bottom:top, lo:hi]
    pixels = np.stack((xx + 0.5, yy + 0.5), axis=2)
    # A large triangle may also extend behind a setback on the same elevation.
    # Sample its original UV for those parts instead of baking the front tower.
    bary = (pixels - vertices[0]) @ np.linalg.inv(vertices[1:] - vertices[:1])
    weights = np.concatenate((1 - bary.sum(2, keepdims=True), bary), axis=2)
    outside = np.any(weights < 0, axis=2)
    weights = np.maximum(weights, 0)
    weights /= weights.sum(2, keepdims=True)
    original = sample_rgb(atlas, weights @ source_uv)
    # Sample the finished face edge everywhere outside its triangle, all the
    # way to the corners of its chart. Shared UV edges then survive Web mipmaps.
    p = weights @ vertices
    centre = vertices.mean(axis=0)
    step = centre - p
    p[outside] += (
        step / np.maximum(np.linalg.norm(step, axis=2, keepdims=True), 1) * 0.9
    )[outside]
    ix, iy = (
        np.floor(p)
        .astype(int)
        .clip([0, 0], np.array(owners.shape[::-1]) - 1)
        .transpose(2, 0, 1)
    )
    use = changed[iy, ix] & ((owners[iy, ix] == face) | outside)
    original[use] = result[iy[use], ix[use]]
    local = vertices - low
    local[:, 1] = top - bottom - local[:, 1]
    return np.array([face]), local[None], original[::-1]


def repaint(identifier, atlas, uv, xyz, kind, *, diagnostics=None):
    if diagnostics is None:
        diagnostics = {}
    diagnostics.update(
        evaluatedPixels=0,
        retainedPixels=0,
        appliedPixels=0,
        rejectedDomains=0,
        peakAddedBoundaryContrast=0.0,
    )
    automatic = identifier not in MANUAL_IDS
    plans = [
        p if len(p) == 5 else (*p, 6 if p[2] is None else 5) for p in PLANS[identifier]
    ]
    cross = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    magnitude = np.linalg.norm(cross, axis=1)
    normal = cross / np.maximum(1e-12, magnitude[:, None])
    axes = normal[:, 0] + 1j * normal[:, 1]
    base = np.rad2deg(np.angle(np.sum(magnitude * axes**4))) / 4
    slots = (kind != 0).astype(int)
    panels = {}
    for index in {j for plan in plans for j in (plan[0], plan[2]) if j is not None}:
        ns = normal
        direction = index
        if index < 0:
            direction = -index - 1
            ns = normal.copy()
            slopes = abs(ns[:, 2]) < 0.72
            lateral = ns[slopes, :2]
            ns[slopes, :2] = lateral / np.maximum(
                0.01, np.linalg.norm(lateral, axis=1, keepdims=True)
            )
            ns[slopes, 2] = 0
        azimuth = base + 90 - 90 * direction
        axis = np.radians(azimuth)
        front = np.array([np.cos(axis), np.sin(axis), 0])
        visible = (ns @ front > 0.94) & (abs(ns[:, 2]) < 0.35) & (slots == 0)
        if not visible.any():
            if automatic:
                continue
            raise ValueError(
                f"{identifier} has no original photograph on reviewed elevation {index}"
            )
        panels[index] = elevation(
            xyz, ns, uv, slots, {0: atlas}, base + 90 - 90 * direction
        )
    charts = []
    reasons = np.zeros(len(uv), "i4")
    for target in sorted({p[0] for p in plans}):
        if target not in panels:
            continue
        panel = panels[target]
        rgb, owners, _, extent = panel
        h, w = owners.shape
        yy, xx = np.indices((h, w), dtype=float)
        x, y = (xx + 0.5) / w, (yy + 0.5) / h
        changed = np.zeros((h, w), bool)
        domain_reasons = np.zeros((h, w), "u1")
        corrected = rgb.copy()
        groups = {}
        for direction, contour, donor, patch, reason in plans:
            if direction != target:
                continue
            key = (donor, tuple(patch) if patch else None, reason)
            groups.setdefault(key, []).append(contour)
        for (donor, patch, reason), contours in groups.items():
            use = np.zeros((h, w), bool)
            for contour in contours:
                if automatic:
                    contour = registered_coordinates(panel, contour)
                use |= inside_polygon(x, y, contour)
            use &= owners >= 0
            if automatic and donor in panels:
                edges = registered_coordinates(panels[donor], [patch[:2], patch[2:]])
                patch = (*edges[0], *edges[1])
            if np.any(use):
                if donor is None and reason == 6:
                    corrected[use] = construction_finish(
                        panels[2], x[use] * extent[0], (1 - y[use]) * extent[1]
                    )
                elif donor is None:
                    corrected[use] = local_finish(panel, x[use], y[use], use)
                elif donor in panels:
                    source = panels[donor]
                    try:
                        patch = covered_patch(source, patch)
                        corrected[use] = sample_donor(
                            source, patch, x[use], y[use], donor != target, extent
                        )
                    except ValueError:
                        if not automatic:
                            raise
                        continue
                else:
                    continue
                domain_reasons[use] = reason
                changed |= use
        if automatic and changed.any():
            corrected, changed = composite_wall(
                rgb, corrected, owners, changed, extent, diagnostics
            )
        np.maximum.at(reasons, owners[changed], domain_reasons[changed])
        for face in np.unique(owners[changed]):
            charts.append(face_chart(atlas, uv[face], face, panel, corrected, changed))
    touched = reasons != 0
    if not touched.any():
        if automatic:
            return atlas, uv, reasons
        raise ValueError(
            f"No photographed facade pixels in {identifier}'s reviewed masks"
        )
    keep = np.flatnonzero(~touched)
    if len(keep):
        charts.extend(photograph_charts(atlas, uv, keep))
    paint, mapped = pack(charts, len(uv))
    return paint, mapped, reasons


def box_total(image, radius):
    # Integral images give a scale in metric facade texels with no optional
    # renderer dependence. Empty pixels are separately normalized below.
    h, w = image.shape[:2]
    yy = np.arange(h)
    xx = np.arange(w)
    y0, y1 = np.maximum(yy - radius, 0), np.minimum(yy + radius + 1, h)
    x0, x1 = np.maximum(xx - radius, 0), np.minimum(xx + radius + 1, w)
    prefix = np.zeros((h + 1, w + 1, *image.shape[2:]), "f8")
    prefix[1:, 1:] = image.cumsum(0, dtype="f8").cumsum(1)
    return (
        prefix[y1[:, None], x1]
        - prefix[y0[:, None], x1]
        - prefix[y1[:, None], x0]
        + prefix[y0[:, None], x0]
    ).astype("f4")


def local_mean(rgb, mask, radius):
    weights = box_total(mask, radius)
    return box_total(rgb * mask[:, :, None], radius) / np.maximum(
        1, weights[:, :, None]
    ), weights


def composite_wall(original, replacement, owners, changed, extent, stats):
    valid = owners >= 0
    rgb = original.astype("f4")
    other = replacement.astype("f4")
    h, w = valid.shape
    scale = min(w / max(0.01, extent[0]), h / max(0.01, extent[1]))
    span = np.sqrt(changed.sum())
    radius = int(np.clip(min(span * 0.18, scale * 2.0), 3, 18))
    target, _ = local_mean(rgb, valid, radius)
    donor, _ = local_mean(other, changed, radius)
    # Aim the low spatial frequencies at the surviving wall beside the patch.
    # Near the middle of a large gap keep broad architectural shadows, while
    # the donor supplies the measured window and panel scale.
    context, support = local_mean(
        rgb, valid & ~changed, max(2 * radius, round(min(span * 0.4, scale * 6)))
    )
    confidence = np.clip(support / (8 * radius**2), 0, 0.65)[:, :, None]
    desired = target * (1 - confidence) + context * confidence
    detail = other - donor
    mapped = np.clip(desired + detail, 0, 255)
    # A foreign dark donor with little recoverable facade detail only paints a
    # flat rectangle. Keep the photograph where the proposed donor cannot
    # match the surrounding exposure or has no structure after correction.
    mismatch = np.mean(abs(desired - donor), axis=2)
    contrast, _ = local_mean(abs(detail), changed, radius)
    reject = changed & (mismatch > 46) & (contrast.mean(2) < 4)
    active = changed & ~reject
    stats["evaluatedPixels"] += int(changed.sum())
    if not active.any():
        stats["rejectedDomains"] += 1
        stats["retainedPixels"] += int(changed.sum())
        return original, active
    # A feather follows the connected mask and spans 1–3 facade metres. Small
    # isolated cells receive little opacity; the physical silhouette stays sharp.
    feather = int(np.clip(min(span * 0.24, scale * 3), 3, 24))
    coverage = box_total(active, feather) / np.maximum(1, box_total(valid, feather))
    alpha = np.clip((coverage - 0.32) / 0.60, 0, 1)
    alpha *= alpha * (3 - 2 * alpha)
    adjacent = ~active & valid
    fringe = adjacent.copy()
    edge = np.zeros(valid.shape, bool)
    for step in range(1, feather + 1):
        prev = fringe.copy()
        fringe[1:] |= prev[:-1]
        fringe[:-1] |= prev[1:]
        fringe[:, 1:] |= prev[:, :-1]
        fringe[:, :-1] |= prev[:, 1:]
        ring = fringe & ~prev & active
        t = step / (feather + 1)
        alpha[ring] *= t * t * (3 - 2 * t)
        if step == 1:
            edge = ring
    # Limit extra contrast on the first row bordering unmodified photography.
    # This constrains exposure jumps without smoothing any original facade.
    difference = mapped - rgb
    magnitude = np.max(abs(difference), axis=2)
    alpha[edge] = np.minimum(alpha[edge], 3 / np.maximum(1, magnitude[edge]))
    if edge.any():
        peak = float(np.max(magnitude[edge] * alpha[edge]))
        stats["peakAddedBoundaryContrast"] = round(
            max(peak, stats["peakAddedBoundaryContrast"]), 2
        )
    use = active & (alpha >= 0.01) & (magnitude * alpha >= 1)
    # A few sub-byte changes do not justify rewriting an otherwise readable
    # photographed triangle and its packed image.
    noticeable = int(np.sum(use & (magnitude * alpha >= 4)))
    minimum = max(6, min(20, round(changed.sum() * 0.02)))
    if noticeable < minimum:
        use[:] = False
    applied = int(use.sum())
    stats["appliedPixels"] += applied
    stats["retainedPixels"] += int(changed.sum()) - applied
    if not applied:
        stats["rejectedDomains"] += 1
    result = original.copy()
    result[use] = (
        np.rint(rgb[use] + alpha[use, None] * difference[use]).clip(0, 255).astype("u1")
    )
    return result, use


def registered_coordinates(panel, coordinates):
    # The screen crops the black border tightly. The editable ortho panel adds
    # a metric texel gutter, which matters on thin side walls.
    _, owner, _, _ = panel
    yy, xx = np.nonzero(owner >= 0)
    if not len(xx):
        return coordinates
    h, w = owner.shape
    low = np.array([xx.min() / w, yy.min() / h])
    span = np.array([(np.ptp(xx) + 1) / w, (np.ptp(yy) + 1) / h])
    return low + np.asarray(coordinates) * span


def local_finish(panel, x, y, excluded):
    photo, owners, _, extent = panel
    visible = owners >= 0
    usable = visible & ~excluded
    if usable.sum() < 8:
        usable = visible
    pixels = photo[usable]
    luma = pixels @ np.array([0.2126, 0.7152, 0.0722])
    wall = np.median(pixels[luma >= np.percentile(luma, 55)], axis=0)
    glass = np.median(pixels[luma <= np.percentile(luma, 32)], axis=0)
    # Search for the floor rhythm in the photographed horizontal courses.
    # If too little survives, a dimension-based panel joint remains editable.
    h = len(photo)
    gray = photo @ np.array([0.2126, 0.7152, 0.0722])
    rows = (gray * usable).sum(1) / np.maximum(1, usable.sum(1))
    spacing = 3.35
    best = 0.42
    for step in range(
        max(2, round(h * 2.5 / max(extent[1], 1))),
        min(h // 3, round(h * 5 / max(extent[1], 1))) + 1,
    ):
        mask = (usable[:-step].sum(1) > 3) & (usable[step:].sum(1) > 3)
        a, b = rows[:-step][mask], rows[step:][mask]
        if len(a) > 8 and min(np.std(a), np.std(b)) > 3:
            corr = float(np.corrcoef(a, b)[0, 1])
            if corr > best:
                spacing, best = step / h * extent[1], corr
    z = (1 - y) * extent[1]
    u = x * extent[0]
    course = (z / spacing) % 1
    bay = max(1.35, min(2.4, extent[0] / max(1, round(extent[0] / 1.8))))
    joints = (u % bay < 0.095) | (course < 0.025)
    glazing = (best > 0.42) & (course > 0.25) & (course < 0.84) & ~joints
    variation = (1.8 * np.sin(np.floor(u / bay) * 1.73) - 7 * joints)[:, None]
    return np.clip(np.where(glazing[:, None], glass, wall) + variation, 0, 255).astype(
        "u1"
    )


def construction_finish(panel, x, z):
    # On this construction site every upright tower face shows the deck viewed
    # from overhead. Its photographed pale lower enclosure supplies only a
    # palette. Panel joints are inferred at architectural scale.
    photo, owners, _, _ = panel
    h, w = owners.shape
    selection = np.s_[int(h * 0.88) : int(h * 0.96), int(w * 0.08) : int(w * 0.37)]
    tint = np.median(photo[selection][owners[selection] >= 0], axis=0)
    panel_x = np.floor(x / 1.82)
    variation = 3 * np.sin(panel_x * 2.31) + 2 * np.cos(z * 0.14 + panel_x * 0.30)
    seams = (np.mod(x, 1.82) < 0.095) | (np.mod(z, 3.64) < 0.08)
    return (tint + (variation - 9 * seams)[:, None]).clip(0, 255).astype(np.uint8)


def recipe_hash(identifier):
    # A changed mask must be applied to the first draft. Applying it onto the
    # previous repair loses the original photograph under the old footprint.
    recipe = (
        "photo-survey-v3" if identifier in MANUAL_IDS else "measured-screen-v2",
        PLANS[identifier],
    )
    return hashlib.sha256(
        json.dumps(recipe, separators=(",", ":")).encode()
    ).hexdigest()
