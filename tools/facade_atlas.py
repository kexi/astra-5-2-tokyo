"""Repair occlusions in the three surveyed facade UV islands visible in the flight."""

from hashlib import sha256
from io import BytesIO

# Fixed source fingerprints protect adjacent islands if PLATEAU republishes a tile.
FACADES = {
    "chiyoda-data435": "c68c4fae5eb574c1a2234d42595a8b480c78862ab51135fafaa1b48647406053",
    "chiyoda-data446": "98c47e2cb622a04863dc84238ceedd2172439c5ecb72087036000adcded811bf",
    "chiyoda-data449": "565eb56c2a04da943640bbe159361ed4f1a08ac7043b1656a240d7d6995bd265",
}


def granroof_frontage(width, height):
    """Reconstruct the east elevation under the original PLATEAU roof geometry."""
    import numpy as np
    from PIL import Image

    u, v = np.meshgrid(
        (np.arange(width) + 0.5) / width, (np.arange(height) + 0.5) / height
    )
    bay = u * 31
    across = bay % 1
    glazing = (bay * 4) % 1
    colour = np.zeros((height, width, 3))
    # Cool sky above the deck and a soft, varying interior behind the lower glazing.
    daylight = 0.5 + 0.5 * np.cos(2 * np.pi * (u * 1.8 + 0.18 * v))
    panels = np.random.default_rng(240).uniform(-1, 1, 124)[
        np.floor(bay * 4).astype(int)
    ]
    reflection = 8 * daylight + 5 * panels + 3 * np.cos(2 * np.pi * (u * 18 + v * 0.7))
    colour[:] = [60, 72, 76] + (reflection - 14 * v)[..., None]

    def paint(mask, rgb):
        weight = np.clip(mask, 0, 1)[..., None]
        colour[:] = colour * (1 - weight) + np.asarray(rgb) * weight

    def stroke(coordinate, half_width, antialias):
        return np.clip(0.5 + (half_width - np.abs(coordinate)) / antialias, 0, 1)

    # V grows downwards from the membrane toward street level.
    paint(stroke(glazing - 0.5, 0.017, 124 / width), [40, 48, 50])
    paint(stroke(v - 0.80, 0.008, 1 / height), [71, 78, 79])
    # Recessed storefronts along the deck catch different amounts of skylight.
    deck = v < 0.655
    shop = np.floor(bay / 2).astype(int)
    finishes = np.array(
        [
            [104, 113, 112],
            [122, 117, 100],
            [92, 107, 110],
            [113, 111, 102],
            [100, 110, 114],
            [118, 113, 105],
        ]
    )
    shade = 12 * daylight + 9 * panels - 24 * np.clip((0.49 - v) / 0.20, 0, 1)
    paint(deck, finishes[shop % len(finishes)] + shade[..., None])
    shop_windows = (v > 0.385) & (v < 0.622)
    paint(shop_windows * stroke(glazing - 0.5, 0.024, 124 / width), [70, 78, 78])
    paint(stroke(v - 0.385, 0.009, 1 / height), [120, 126, 124])
    # Slender aluminium struts meet the deep fascia below the roof.
    braces = stroke(np.abs(across - 0.5) - (0.49 - v) * 1.55, 0.014, 31 / width)
    paint((v < 0.49) * braces, [155, 162, 159])
    paint(stroke(v - 0.51, 0.007, 1 / height), [80, 89, 86])
    soffit = v < 0.25 + 0.035 * np.cos(2 * np.pi * u)
    paint(
        soffit, [120, 126, 123] + (21 * v - 16 * np.cos(across * 2 * np.pi))[..., None]
    )
    columns = stroke(np.minimum(across, 1 - across), 0.019, 31 / width)
    paint(columns, [161, 165, 161])
    paint(stroke(across - 0.008, 0.005, 31 / width), [194, 197, 189])
    paint(stroke(across - 0.032, 0.009, 31 / width), [68, 76, 76])
    rail_glass = (v > 0.55) & (v < 0.65)
    paint(0.49 * rail_glass, [120, 142, 145])
    paint(stroke(v - 0.55, 0.005, 1 / height), [168, 174, 171])
    paint(stroke(v - 0.655, 0.014, 1 / height), [121, 127, 127])
    paint(stroke(v - 0.678, 0.008, 1 / height), [57, 64, 65])
    return Image.fromarray(np.uint8(np.clip(colour, 0, 255)))


def repair_facade(tile_id, data):
    from PIL import Image, ImageEnhance

    fingerprint = sha256(data).hexdigest()
    if fingerprint != FACADES[tile_id]:
        raise ValueError(
            f"Recheck {tile_id} UV islands against source image {fingerprint}"
        )
    atlas = Image.open(BytesIO(data)).convert("RGB")
    if tile_id == "chiyoda-data446":
        # The station has symmetric brick wings. Both rectangles are on the west elevation.
        wing = atlas.crop((417, 865, 549, 996))
        atlas.paste(ImageEnhance.Brightness(wing).enhance(0.92), (141, 865))
    elif tile_id == "chiyoda-data435":
        # Clone from eleven storeys above to preserve the measured 36-pixel pitch.
        atlas.paste(atlas.crop((0, 276, 512, 629)), (0, 672))
    else:
        # The 1024-square source dedicates 245 rows to this ~235 m east-facing plane.
        # Supersampling the new frontage retains slim railings and mullions in the film.
        atlas = atlas.resize((2048, 2048), Image.Resampling.LANCZOS)
        atlas.paste(granroof_frontage(2048, 492), (0, 538))

    if atlas.width > 2048:
        atlas.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    output = BytesIO()
    atlas.save(output, format="WEBP", quality=93, method=5)
    return output.getvalue()
