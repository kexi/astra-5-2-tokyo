"""Identify roof-to-wall projection failures and build metric infill charts."""

import zlib

import numpy as np


def repeated_roof(rgb, mapped, interior, walls, size):
    """Check a rigid copy of a roof photograph in a separately packed wall island."""
    mask = np.zeros(len(mapped), bool)
    y, x = np.nonzero(interior)
    if len(y) < 35:
        return mask
    ix = np.minimum(
        ((np.arange(size[0]) + 0.5) * rgb.shape[1] / size[0]).astype(int),
        rgb.shape[1] - 1,
    )
    iy = np.minimum(
        ((np.arange(size[1]) + 0.5) * rgb.shape[0] / size[1]).astype(int),
        rgb.shape[0] - 1,
    )
    thumb = rgb[iy[:, None], ix].astype(np.int16)
    roof_pixels = thumb[y, x]
    bary = np.array(
        [
            (a / 10, b / 10, 1 - (a + b) / 10)
            for a, b in [
                (3, 3),
                (1, 1),
                (1, 4),
                (1, 7),
                (2, 6),
                (3, 1),
                (3, 5),
                (4, 2),
                (4, 4),
                (5, 1),
                (5, 3),
                (6, 2),
                (7, 1),
                (2, 2),
                (2, 5),
            ]
        ]
    )
    extent = np.abs(np.linalg.det(mapped[:, 1:] - mapped[:, :1]))
    for i in np.flatnonzero(walls & (extent > 125)):
        sample = np.floor(bary @ mapped[i]).astype(int).clip([0, 0], size - 1)
        colors = thumb[sample[:, 1], sample[:, 0]]
        # A plain wall and a plain roof often share one shade; use textured
        # interiors spanning a substantial tonal range, with diverse samples.
        if (
            np.max(np.std(colors, axis=0)) < 12
            or len(np.unique(colors // 8, axis=0)) < 7
        ):
            continue
        anchor = abs(roof_pixels - colors[0]).max(1) <= 5
        if not np.any(anchor):
            continue
        origin = np.column_stack((x[anchor], y[anchor]))
        if len(origin) > 200:
            continue
        delta = sample - sample[0]
        for flip in (1, -1):
            offset = delta * [flip, 1]
            for _ in range(4):
                probe = origin[:, None, :] + offset
                fits = ((probe >= 0) & (probe < size)).all((1, 2))
                coords = probe[fits]
                if len(coords):
                    xx, yy = coords[:, :, 0], coords[:, :, 1]
                    owns = interior[yy, xx].all(1)
                    diff = abs(thumb[yy[owns], xx[owns]] - colors)
                    if len(diff) and np.any(
                        (diff.mean((1, 2)) < 4.0) & (diff.max((1, 2)) < 18)
                    ):
                        mask[i] = True
                        break
                offset = np.stack((-offset[:, 1], offset[:, 0]), axis=1)
            if mask[i]:
                break
    return mask


def plane_coords(points, normals, areas):
    xy = points[:, :, :2] - points[:, :, :2].min((0, 1))
    z = points[:, :, 2] - points[:, :, 2].min()
    n = normals[:, 0] + 1j * normals[:, 1]
    axis = np.angle(np.sum(areas * n**4)) / 4
    a = np.array([np.cos(axis), np.sin(axis)])
    b = np.array([-a[1], a[0]])
    xx, yy = xy @ a, xy @ b
    xx -= xx.min()
    yy -= yy.min()
    wall_x = np.where(
        (abs(normals[:, :2] @ a) > abs(normals[:, :2] @ b))[:, None], yy, xx
    )
    return np.stack((wall_x, z), axis=2), np.stack((xx, yy), axis=2)


def classify_photo(rgb, uv, ids, normals, areas, wall_coords):
    # Sample interiors, not shared silhouette boundaries. A wall and a roof
    # reading the same interior texels is strong evidence of planar projection.
    h, w = rgb.shape[:2]
    p = uv[ids] * [w, h]
    floors = (normals[ids, 2] > 0.62) & (areas[ids] > 0.5)
    walls = (abs(normals[ids, 2]) < 0.28) & (areas[ids] > 1.5)
    reason = np.zeros(len(ids), dtype=np.uint8)
    if not walls.any():
        return reason
    from blender_texture_charts import coverage

    if floors.any():
        # Downsample large atlases only for the overlap test. A one-pixel
        # dilation on a coarse map produces false hits on neighbouring islands.
        scale = min(1.0, 640 / max(w, h))
        size = np.maximum(1, np.ceil(np.array([w, h]) * scale).astype(int))
        mapped = uv[ids] * size
        roof = coverage(mapped[floors], *size)
        interior = roof.copy()
        for axis in (0, 1):
            interior &= np.roll(roof, 2, axis) & np.roll(roof, -2, axis)
        bary = np.array(
            [
                [0.7, 0.15, 0.15],
                [0.15, 0.7, 0.15],
                [0.15, 0.15, 0.7],
                [0.333, 0.333, 0.334],
                [0.45, 0.45, 0.1],
                [0.45, 0.1, 0.45],
                [0.1, 0.45, 0.45],
            ]
        )
        xy = np.einsum("ij,tjk->tik", bary, mapped).astype(int).clip([0, 0], size - 1)
        overlap = interior[xy[:, :, 1], xy[:, :, 0]].mean(axis=1)
        reason[walls & (overlap >= 0.85)] = 1
        reason[repeated_roof(rgb, mapped, interior, walls & (reason == 0), size)] = 4
    # Severely collapsed vertical sampling on broad walls turns roof edges
    # into long vertical bands. Compare texel density along physical axes.
    use = np.flatnonzero(walls)
    xy = wall_coords[ids[use]]
    metric = xy[:, 1:] - xy[:, :1]
    pix = p[use, 1:] - p[use, :1]
    usable = np.abs(np.linalg.det(metric)) > 1
    use = use[usable]
    if len(use):
        gradient = np.linalg.solve(metric[usable], pix[usable])
        horizontal = np.linalg.norm(gradient[:, 0], axis=1)
        vertical = np.linalg.norm(gradient[:, 1], axis=1)
        span = np.ptp(xy[usable, :, 1], axis=1)
        collapsed = (
            (span > 4)
            & (horizontal > 0.65)
            & (vertical < 0.12)
            & (vertical < 0.04 * horizontal)
        )
        reason[use[collapsed]] = 2
    return reason


def donor_patch(rgb, uv, coords, eligible):
    # An axis-aligned square in the incircle takes only this photographed wall,
    # not an adjacent triangle. Its repeat spacing stays in metres.
    ids = np.flatnonzero(eligible)
    if not len(ids):
        return None
    v = coords[ids]
    edges = np.linalg.norm(v - np.roll(v, 1, axis=1), axis=2)
    twice_area = abs(np.linalg.det(v[:, 1:] - v[:, :1]))
    radius = twice_area / np.maximum(edges.sum(1), 1e-8)
    i = int(np.argmax(radius))
    r = radius[i]
    if r < 2.5:
        return None
    tr = v[i]
    weights = np.linalg.norm(np.roll(tr, -1, axis=0) - np.roll(tr, 1, axis=0), axis=1)
    origin = (tr * weights[:, None]).sum(0) / weights.sum() - r * 0.66
    basis = np.linalg.solve(tr[1:] - tr[:1], uv[ids[i], 1:] - uv[ids[i], :1])
    if np.linalg.norm(basis * np.array(rgb.shape[1::-1]), axis=1).min() * r < 3:
        return None
    return r, origin, uv[ids[i], 0] + (origin - tr[0]) @ basis, basis, rgb


def surface_chart(xy, ids, base, kind, seed, donor=None):
    # Use a common world frame for all triangles of each finish. Facade joints
    # and floor courses continue across the two triangles forming a wall.
    coords = xy[ids]
    lo = coords.min((0, 1))
    span = np.maximum(coords.max((0, 1)) - lo, 0.4)
    dims = np.clip(np.ceil(span * 3.5).astype(int), 20, 256)
    w, h = dims
    y, x = np.mgrid[:h, :w]
    x = lo[0] + (x + 0.5) * span[0] / w
    y = lo[1] + (y + 0.5) * span[1] / h
    if donor is not None:
        radius, start, origin, basis, rgb = donor
        meters = (np.stack((x, y), axis=-1) - start) % (radius * 1.32)
        sample = (origin + meters @ basis) * rgb.shape[1::-1] - 0.5
        low = np.floor(sample).astype(int)
        frac = (sample - low).clip(0, 1)
        color = np.zeros((h, w, 3))
        for ox, oy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            pix = (low + [ox, oy]).clip([0, 0], [rgb.shape[1] - 1, rgb.shape[0] - 1])
            weight = (frac[:, :, 0] if ox else 1 - frac[:, :, 0]) * (
                frac[:, :, 1] if oy else 1 - frac[:, :, 1]
            )
            color += rgb[pix[:, :, 1], pix[:, :, 0]] * weight[:, :, None]
    else:
        rng = np.random.default_rng(seed + kind)
        grain = rng.normal(0, 1.7, (h, w))
        shade = 2 * np.sin(x * 0.9 + y * 0.36 + seed % 19) + grain
        if kind in (1, 4):
            floors = max(1, round(span[1] / 3.5))
            course = span[1] / floors
            bays = max(1, round(span[0] / (2.9 + (seed % 23) * 0.04)))
            u = ((x - lo[0]) / span[0] * bays) % 1
            v = ((y - lo[1]) / course) % 1
            seam = (u < 0.055) | (v < 0.055)
            glazing = (
                (u > 0.18)
                & (u < 0.82)
                & (v > 0.28)
                & (v < 0.82)
                & (span[1] > 3.8)
                & (span[0] > 3.5)
            )
            tint = np.asarray(base)[None, None, :]
            panes = np.clip(tint * np.array([0.63, 0.69, 0.75]) + [4, 6, 9], 16, 202)
            color = np.where(glazing[:, :, None], panes, tint) + shade[:, :, None]
            color -= (seam * 12)[:, :, None]
        else:
            pitch = 1.3 if kind == 2 else 3.8
            seam = (np.mod(x + 0.25, pitch) < max(0.06, span[0] / w * 0.45)) | (
                np.mod(y + 0.4, pitch * 3) < max(0.06, span[1] / h * 0.45)
            )
            color = np.asarray(base)[None, None, :] + (shade - 8 * seam)[:, :, None]
    tex = (coords - lo) / span * (dims - 1) + 0.5
    return ids, tex, color.round().clip(0, 255).astype(np.uint8)


def building_seed(building_id):
    return zlib.crc32(building_id.encode())
