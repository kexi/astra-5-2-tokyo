"""Pack the photographed UV islands of one building without resampling its pixels."""

import struct
import zlib

import numpy as np

GUTTER = 3


def png(rgb, identifier):
    def chunk(key, data):
        return (
            struct.pack(">I", len(data))
            + key
            + data
            + struct.pack(">I", zlib.crc32(key + data))
        )

    h, w = rgb.shape[:2]
    rows = rgb[::-1].copy()
    rows[:, 1:] -= rows[:, :-1]
    encoded = b"".join(b"\x01" + row.tobytes() for row in rows)
    header = struct.pack(">2I5B", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"tEXt", b"BuildingID\x00" + identifier.encode("ascii"))
        + chunk(b"IDAT", zlib.compress(encoded, 3))
        + chunk(b"IEND", b"")
    )


def coverage(tri, w, h):
    """Mark the texels touched by the footprint of a bilinearly sampled triangle."""
    low = np.clip(np.floor(tri[:, :, 1].min(1) - 1).astype(int), 0, h - 1)
    high = np.clip(np.ceil(tri[:, :, 1].max(1) + 1).astype(int), low, h - 1)
    counts = high - low + 1
    ids = np.repeat(np.arange(len(tri)), counts)
    yy = np.arange(len(ids)) + np.repeat(low - np.cumsum(counts) + counts, counts)
    left = np.full(len(ids), np.inf)
    right = np.full(len(ids), -np.inf)
    for i in range(3):
        a, b = tri[ids, i], tri[ids, (i + 1) % 3]
        dy = b[:, 1] - a[:, 1]
        crossing = (np.maximum(a[:, 1], b[:, 1]) >= yy - 0.51) & (
            np.minimum(a[:, 1], b[:, 1]) <= yy + 1.51
        )
        den = np.where(abs(dy) > 1e-12, dy, 1)
        t0 = np.clip((yy - 0.51 - a[:, 1]) / den, 0, 1)
        t1 = np.clip((yy + 1.51 - a[:, 1]) / den, 0, 1)
        x0 = a[:, 0] + t0 * (b[:, 0] - a[:, 0])
        x1 = np.where(abs(dy) > 1e-12, a[:, 0] + t1 * (b[:, 0] - a[:, 0]), b[:, 0])
        left = np.minimum(left, np.where(crossing, np.minimum(x0, x1), np.inf))
        right = np.maximum(right, np.where(crossing, np.maximum(x0, x1), -np.inf))
    ok = np.isfinite(left)
    x0 = np.clip(np.floor(left[ok] - 0.51).astype(int), 0, w - 1)
    x1 = np.clip(np.ceil(right[ok] + 0.51).astype(int), x0 + 1, w)
    diff = np.zeros((h, w + 1), dtype=np.int32)
    np.add.at(diff, (yy[ok], x0), 1)
    np.add.at(diff, (yy[ok], x1), -1)
    return np.cumsum(diff[:, :w], axis=1, dtype=np.int32) > 0


def regions(tri, w, h):
    # Rectangular bounds on a coarse grid join contiguous UV islands without
    # forcing two unrelated corners of a shared city atlas into one huge crop.
    unit = 12
    gw, gh = (w + unit - 1) // unit, (h + unit - 1) // unit
    lo = np.clip(((tri.min(1) - 1.5) // unit).astype(int), 0, [gw - 1, gh - 1])
    hi = np.clip(((tri.max(1) + 1.5) // unit).astype(int), lo, [gw - 1, gh - 1]) + 1
    change = np.zeros((gh + 1, gw + 1), dtype=np.int32)
    for a, b, val in [(lo, lo, 1), (hi, hi, 1), (lo, hi, -1), (hi, lo, -1)]:
        np.add.at(change, (a[:, 1], b[:, 0]), val)
    occupied = change.cumsum(0).cumsum(1)[:-1, :-1] > 0
    labels = np.full((gh, gw), -1, dtype=np.int32)
    components = []
    for y, x in zip(*np.nonzero(occupied), strict=True):
        if labels[y, x] >= 0:
            continue
        tag = len(components)
        stack = [(y, x)]
        labels[y, x] = tag
        while stack:
            py, px = stack.pop()
            for ny, nx in ((py - 1, px), (py + 1, px), (py, px - 1), (py, px + 1)):
                if (
                    0 <= ny < gh
                    and 0 <= nx < gw
                    and occupied[ny, nx]
                    and labels[ny, nx] < 0
                ):
                    labels[ny, nx] = tag
                    stack.append((ny, nx))
        components.append(tag)
    ids = labels[lo[:, 1], lo[:, 0]]
    return [np.flatnonzero(ids == i) for i in components]


def photograph_charts(rgb, uv, indices):
    h, w = rgb.shape[:2]
    tri = uv[indices] * [w, h]
    for group in regions(tri, w, h):
        coords = tri[group]
        lower = np.clip(
            np.floor(coords.min((0, 1)) - 1.5).astype(int), 0, [w - 1, h - 1]
        )
        upper = np.clip(
            np.ceil(coords.max((0, 1)) + 1.5).astype(int), lower + 1, [w, h]
        )
        left, bottom = lower
        right, top = upper
        local = coords - lower
        source = rgb[bottom:top, left:right]
        keep = coverage(local, right - left, top - bottom)
        pixels = np.where(keep[:, :, None], source, 0).astype(np.uint8)
        # Extend each silhouette inward from its own sampled edge. The packing
        # background never contains another building's disconnected islands.
        for _ in range(GUTTER):
            for axis in (0, 1):
                for shift in (-1, 1):
                    hit = ~keep & np.roll(keep, shift, axis)
                    edge = [slice(None), slice(None)]
                    edge[axis] = 0 if shift == 1 else -1
                    hit[tuple(edge)] = False
                    pixels[hit] = np.roll(pixels, shift, axis)[hit]
                    keep |= hit
        yield indices[group], local, pixels


def pack(charts, triangle_count):
    shapes = np.array(
        [[c[2].shape[1] + 2 * GUTTER, c[2].shape[0] + 2 * GUTTER] for c in charts]
    )
    width = max(
        int(shapes[:, 0].max()), int(np.ceil(np.sqrt(np.prod(shapes, axis=1).sum())))
    )
    positions = {}
    x = y = row = used = 0
    for i in np.argsort(-shapes[:, 1], kind="stable"):
        w, h = shapes[i]
        if x and x + w > width:
            x = 0
            y += row
            row = 0
        positions[i] = (x, y)
        used = max(used, x + w)
        x += w
        row = max(row, h)
    height = y + row
    atlas = np.zeros((height, used, 3), dtype=np.uint8)
    uv = np.empty((triangle_count, 3, 2), dtype=np.float32)
    for i, (ids, local, pic) in enumerate(charts):
        x, y = positions[i]
        w, h = shapes[i]
        atlas[y : y + h, x : x + w] = np.pad(
            pic, ((GUTTER, GUTTER), (GUTTER, GUTTER), (0, 0)), mode="edge"
        )
        uv[ids] = (local + [x + GUTTER, y + GUTTER]) / [used, height]
    return atlas, uv
