"""Locally replace photogrammetric occlusions on reviewed Marunouchi and Otemachi elevations."""

import bpy
import numpy as np
from omy_facade_blocks import EXTRA_REPAIRS


def rectangle(left, top, right, bottom):
    return [(left, top), (right, top), (right, bottom), (left, bottom)]


# Front-facing orthographic elevations, angles measured east towards north.
# Cutouts and donor rectangles run left/right and top/bottom in each elevation.
# The image underneath every donor is the unmodified photograph of this building.
REPAIRS = {
    **EXTRA_REPAIRS,
    "丸の内パークビルディング": (
        -13.7,
        [
            (
                0,
                [(0.20, 0.707), (0.753, 0.707), (0.722, 0.858), (0.625, 1), (0.202, 1)],
                0,
                (0.272, 0.173, 0.9, 0.683),
                0.89,
            ),
            (1, rectangle(0.014, 0.847, 0.56, 1), 1, (0.614, 0.84, 0.947, 0.982), 1),
            (2, rectangle(0.0, 0.32, 0.99, 0.828), 2, (0.018, 0.103, 0.968, 0.309), 1),
        ],
    ),
    "東京ビルディング": (
        -15.26,
        [
            (0, rectangle(0, 0.781, 1, 1), 0, (0.045, 0.339, 0.958, 0.757), 0.9),
            (
                2,
                [
                    (0.119, 0.389),
                    (0.853, 0.389),
                    (0.853, 1),
                    (0, 1),
                    (0, 0.742),
                    (0.076, 0.701),
                ],
                2,
                (0.041, 0.14, 0.837, 0.385),
                1,
            ),
        ],
    ),
    "星のや東京": (
        -7.3,
        [
            (
                0,
                [
                    (0.365, 0.676),
                    (0.995, 0.676),
                    (0.995, 0.982),
                    (0.488, 0.982),
                    (0.377, 0.923),
                ],
                0,
                (0.03, 0.381, 0.981, 0.665),
                1,
            ),
            (1, rectangle(0, 0, 1, 0.995), 0, (0.095, 0.051, 0.506, 0.676), 0.73),
            (
                2,
                [(0.158, 0), (0.998, 0), (0.998, 1), (0.092, 1), (0.132, 0.152)],
                0,
                (0.078, 0.044, 0.503, 0.676),
                0.58,
            ),
            (
                3,
                rectangle(0.002, 0.625, 0.623, 0.898),
                3,
                (0.114, 0.049, 0.816, 0.405),
                1,
            ),
        ],
    ),
    "三菱ビル": (
        -15,
        [
            (
                1,
                rectangle(0.295, 0.01, 0.68, 0.164),
                2,
                (0.267, 0.051, 0.726, 0.161),
                0.83,
            ),
            (
                1,
                rectangle(0.137, 0.163, 0.95, 0.861),
                0,
                (0.15, 0.232, 0.855, 0.836),
                0.87,
            ),
            (
                2,
                rectangle(0.128, 0.502, 0.88, 0.992),
                0,
                (0.15, 0.232, 0.855, 0.836),
                0.96,
            ),
            (
                3,
                rectangle(0.312, 0.008, 0.607, 0.164),
                2,
                (0.267, 0.051, 0.726, 0.161),
                0.88,
            ),
            (
                3,
                rectangle(0.031, 0.165, 0.625, 0.867),
                0,
                (0.15, 0.232, 0.855, 0.836),
                0.90,
            ),
        ],
    ),
    "丸の内ビルディング": (
        -14.98,
        [
            (
                1,
                [
                    (0.084, 0.628),
                    (0.295, 0.628),
                    (0.295, 0.572),
                    (0.678, 0.572),
                    (0.723, 0.823),
                    (0.085, 0.823),
                ],
                1,
                (0.164, 0.231, 0.884, 0.553),
                1,
            ),
            (
                1,
                [(0.001, 0.815), (0.408, 0.815), (0.292, 0.993), (0.001, 0.993)],
                0,
                (0.053, 0.854, 0.959, 0.986),
                0.78,
            ),
            (
                2,
                [
                    (0.59, 0.533),
                    (0.667, 0.533),
                    (0.667, 0.787),
                    (0.707, 0.815),
                    (0.608, 0.835),
                    (0.533, 0.835),
                ],
                2,
                (0.177, 0.157, 0.616, 0.522),
                1,
            ),
            (2, rectangle(0.05, 0.815, 0.92, 1), 0, (0.053, 0.854, 0.959, 0.986), 0.82),
        ],
    ),
    "新大手町ビル": (
        -14.1,
        [
            (
                1,
                rectangle(0.003, 0.295, 1, 0.997),
                0,
                (0.107, 0.361, 0.95, 0.917),
                0.71,
            ),
            (
                2,
                rectangle(0.01, 0.341, 0.903, 0.997),
                0,
                (0.107, 0.361, 0.95, 0.917),
                0.8,
            ),
            (
                3,
                rectangle(0.063, 0.284, 0.60, 0.997),
                3,
                (0.621, 0.331, 0.89, 0.921),
                1,
            ),
        ],
    ),
    "三菱UFJ信託銀行本店ビル": (
        -15.3,
        [
            (
                0,
                [(0.26, 0.232), (0.525, 0.22), (0.407, 0.763), (0.263, 0.763)],
                0,
                (0.645, 0.12, 0.771, 0.769),
                1,
            ),
            (
                1,
                rectangle(0.094, 0.234, 0.912, 0.751),
                1,
                (0.12, 0.079, 0.968, 0.228),
                1,
            ),
            (
                2,
                rectangle(0.182, 0.167, 0.716, 0.644),
                2,
                (0.213, 0.073, 0.687, 0.157),
                1,
            ),
            (
                3,
                [
                    (0, 0.154),
                    (0.574, 0.154),
                    (0.558, 0.54),
                    (0.602, 0.54),
                    (0.602, 0.761),
                    (0, 0.761),
                ],
                3,
                (0.61, 0.12, 0.853, 0.763),
                1,
            ),
        ],
    ),
    "丸の内永楽ビルディング": (
        -15.1,
        [
            (0, rectangle(0.002, 0.775, 0.563, 1), 0, (0.621, 0.792, 0.923, 0.992), 1),
            (
                1,
                [
                    (0.062, 0.095),
                    (0.588, 0.095),
                    (0.588, 0.788),
                    (0.29, 0.788),
                    (0.29, 0.838),
                    (0.014, 0.838),
                    (0.014, 0.724),
                    (0.062, 0.724),
                ],
                1,
                (0.624, 0.133, 0.842, 0.766),
                1,
            ),
            (
                2,
                [
                    (0.146, 0.449),
                    (0.5, 0.449),
                    (0.527, 0.642),
                    (0.727, 0.642),
                    (0.727, 0.786),
                    (0.146, 0.786),
                ],
                2,
                (0.152, 0.179, 0.719, 0.441),
                1,
            ),
            (
                3,
                [
                    (0.071, 0.228),
                    (0.758, 0.228),
                    (0.758, 0.796),
                    (0.87, 0.836),
                    (0.813, 1),
                    (0.374, 1),
                    (0.374, 0.789),
                    (0.07, 0.789),
                ],
                3,
                (0.138, 0.118, 0.832, 0.224),
                1,
            ),
        ],
    ),
    "丸ノ内ホテル": (
        -20.8,
        [
            (
                1,
                rectangle(0.02, 0.0, 0.423, 0.54),
                0,
                (0.165, 0.108, 0.816, 0.468),
                0.79,
            ),
            (
                1,
                rectangle(0.025, 0.54, 0.427, 1),
                0,
                (0.154, 0.635, 0.821, 0.858),
                0.74,
            ),
            (
                1,
                rectangle(0.432, 0.481, 0.986, 1),
                3,
                (0.394, 0.592, 0.599, 0.975),
                0.87,
            ),
            (
                2,
                [
                    (0.038, 0.459),
                    (0.327, 0.459),
                    (0.321, 0.57),
                    (0.178, 0.676),
                    (0.04, 0.672),
                ],
                2,
                (0.083, 0.149, 0.266, 0.44),
                1,
            ),
            (
                2,
                rectangle(0.053, 0.672, 0.95, 1),
                3,
                (0.394, 0.592, 0.599, 0.975),
                0.84,
            ),
            (
                3,
                [(0.005, 0.604), (0.306, 0.602), (0.305, 0.994), (0.005, 0.994)],
                3,
                (0.397, 0.59, 0.60, 0.978),
                1,
            ),
        ],
    ),
    "大手町フィナンシャルシティ サウスタワー": (
        -6.7,
        [
            (
                1,
                rectangle(0.001, 0.279, 0.82, 0.998),
                1,
                (0.034, 0.1711, 0.81, 0.2434),
                1,
            ),
            (
                2,
                [
                    (0.171, 0.661),
                    (0.926, 0.661),
                    (0.926, 0.958),
                    (0.99, 0.993),
                    (0.177, 0.993),
                ],
                2,
                (0.156, 0.169, 0.867, 0.64),
                1,
            ),
            (
                3,
                [
                    (0.115, 0.35),
                    (0.572, 0.35),
                    (0.575, 0.934),
                    (0.407, 0.934),
                    (0.389, 0.993),
                    (0.004, 0.993),
                    (0.006, 0.922),
                    (0.113, 0.922),
                ],
                3,
                (0.601, 0.146, 0.933, 0.98),
                1,
            ),
        ],
    ),
    "大手町パークビルディング": (
        -12.3,
        [
            (
                1,
                rectangle(0, 0, 0.465, 1),
                3,
                (0.56, 0.06, 0.88, 0.42),
                0.87,
            ),
            (1, rectangle(0.502, 0.397, 1, 1), 3, (0.56, 0.06, 0.88, 0.42), 0.93),
            (3, rectangle(0, 0.317, 0.414, 1), 3, (0.015, 0.25, 0.477, 0.318), 1),
            (
                3,
                rectangle(0.495, 0.414, 0.924, 1),
                3,
                (0.506, 0.054, 0.92, 0.395),
                1,
                (0.884, 0.917),
            ),
        ],
    ),
    "大手町フィナンシャルシティ グランキューブ": (
        -8.3,
        [
            (
                0,
                [
                    (0.217, 0.295),
                    (0.875, 0.288),
                    (0.875, 0.72),
                    (0.769, 0.72),
                    (0.769, 1),
                    (0.216, 1),
                ],
                0,
                (0.025, 0.143, 0.952, 0.283),
                1,
            ),
            (
                2,
                [(0, 0.545), (0.592, 0.545), (0.589, 0.994), (0, 0.994)],
                2,
                (0.019, 0.147, 0.468, 0.534),
                1,
            ),
            (
                3,
                [(0, 0.355), (0.504, 0.363), (0.46, 0.555), (0.431, 1), (0, 1)],
                3,
                (0.022, 0.127, 0.429, 0.342),
                1,
            ),
            (
                3,
                [
                    (0.758, 0.555),
                    (0.966, 0.55),
                    (0.953, 0.85),
                    (0.928, 0.85),
                    (0.928, 0.993),
                    (0.736, 0.993),
                ],
                3,
                (0.506, 0.144, 0.955, 0.538),
                1,
            ),
        ],
    ),
    "大手町フィナンシャルシティ ノースタワー": (
        -6.75,
        [
            (
                2,
                [
                    (0, 0.116),
                    (0.494, 0.116),
                    (0.494, 0.186),
                    (0.993, 0.186),
                    (0.993, 0.993),
                    (0, 0.993),
                ],
                0,
                (0.048, 0.014, 0.94, 0.985),
                0.83,
            ),
            (
                3,
                [
                    (0, 0),
                    (0.846, 0),
                    (0.846, 0.849),
                    (0.808, 0.849),
                    (0.808, 1),
                    (0, 1),
                ],
                1,
                (0.12, 0.022, 0.89, 0.978),
                1.08,
            ),
        ],
    ),
    "常盤橋タワー": (
        -17.02,
        [
            (
                0,
                [(0.022, 0.784), (0.395, 0.818), (0.386, 1), (0.019, 1)],
                0,
                (0.39, 0.495, 0.70, 0.992),
                1,
            ),
            (
                3,
                [
                    (0, 0.825),
                    (0.073, 0.838),
                    (0.209, 0.539),
                    (0.70, 0.56),
                    (0.795, 0.664),
                    (0.752, 0.803),
                    (0.747, 1),
                    (0, 1),
                ],
                3,
                (0.097, 0.31, 0.864, 0.56),
                1,
            ),
        ],
    ),
}


def inside_polygon(x, y, contour):
    inside = np.zeros(x.shape, dtype=bool)
    x0, y0 = contour[-1]
    for x1, y1 in contour:
        if y0 != y1:
            inside ^= ((y0 > y) != (y1 > y)) & (
                x < (x1 - x0) * (y - y0) / (y1 - y0) + x0
            )
        x0, y0 = x1, y1
    return inside


def photograph(material):
    if not material or not material.node_tree:
        return None
    return next(
        (n for n in material.node_tree.nodes if n.type == "TEX_IMAGE" and n.image), None
    )


def texture_pixels(image):
    width, height = image.size
    rgba = np.empty((height, width, 4), dtype=np.float32)
    image.pixels.foreach_get(rgba.ravel())
    return (rgba[:, :, :3] * 255).round().clip(0, 255).astype(np.uint8)


def elevation(xyz, normals, uv, slots, pictures, azimuth):
    angle = np.radians(azimuth)
    front = np.array([np.cos(angle), np.sin(angle), 0])
    right = np.array([-np.sin(angle), np.cos(angle), 0])
    selected = np.flatnonzero(
        (normals @ front > 0.94)
        & (abs(normals[:, 2]) < 0.35)
        & np.isin(slots, list(pictures))
    )
    u, z = xyz @ right, xyz[:, :, 2]
    left, top = u[selected].min(), z[selected].max()
    extent = np.array([u[selected].max() - left, top - z[selected].min()])
    scale = min(5, 968 / max(extent))
    width, height = np.ceil(extent * scale).astype(int) + 4
    screen = np.stack(((u - left) * scale + 1, (top - z) * scale + 1), axis=2)
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    depth = np.full((height, width), -np.inf)
    owners = np.full((height, width), -1, dtype=np.int32)
    distance = xyz @ front
    for face in selected:
        triangle = screen[face]
        basis = (triangle[1:] - triangle[0]).T
        if abs(np.linalg.det(basis)) < 0.02:
            continue
        lo = np.maximum(0, np.floor(triangle.min(axis=0)).astype(int))
        hi = np.minimum([width, height], np.ceil(triangle.max(axis=0)).astype(int) + 1)
        region = np.s_[lo[1] : hi[1], lo[0] : hi[0]]
        yy, xx = np.mgrid[lo[1] : hi[1], lo[0] : hi[0]]
        delta = np.stack(
            (xx + 0.5 - triangle[0, 0], yy + 0.5 - triangle[0, 1]), axis=-1
        )
        w = delta @ np.linalg.inv(basis).T
        inside = (w[:, :, 0] >= 0) & (w[:, :, 1] >= 0) & (w.sum(axis=2) <= 1)
        d = distance[face, 0] + w @ (distance[face, 1:] - distance[face, 0])
        visible = inside & (d > depth[region])
        if not visible.any():
            continue
        ts = uv[face, 0] + w[visible] @ (uv[face, 1:] - uv[face, 0])
        pic = pictures[slots[face]]
        tx = np.clip((ts[:, 0] * pic.shape[1]).astype(int), 0, pic.shape[1] - 1)
        ty = np.clip((ts[:, 1] * pic.shape[0]).astype(int), 0, pic.shape[0] - 1)
        rgb[region][visible] = pic[ty, tx]
        depth[region][visible] = d[visible]
        owners[region][visible] = face
    return rgb, owners, screen, extent


def sample_donor(panel, area, x, y, opposite, dest_extent):
    source, owners, _, source_extent = panel
    height, width = source.shape[:2]
    left, top, right, bottom = area
    # Keep the floor level and physical window pitch wherever the donor covers
    # this location; continue the same measured patch beyond its photographed span.
    if opposite:
        x = 1 - x
    x = 0.5 + (x - 0.5) * dest_extent[0] / source_extent[0]
    y = 1 - (1 - y) * dest_extent[1] / source_extent[1]
    tx = ((left + (x - left) % (right - left)) * width).astype(int).clip(0, width - 1)
    ty = ((top + (y - top) % (bottom - top)) * height).astype(int).clip(0, height - 1)
    bad = owners[ty, tx] < 0
    # A setback can cut into the edge of a donor patch. Sample the nearest
    # photographed point on its row instead of filling it with a guessed colour.
    for row in np.unique(ty[bad]):
        cols = np.flatnonzero(owners[row] >= 0)
        cols = cols[(cols >= left * width) & (cols <= right * width)]
        if not len(cols):
            raise ValueError(f"Donor has an empty row: {row}")
        at = bad & (ty == row)
        idx = np.searchsorted(cols, tx[at]).clip(0, len(cols) - 1)
        prev = np.maximum(0, idx - 1)
        tx[at] = np.where(
            abs(cols[prev] - tx[at]) < abs(cols[idx] - tx[at]), cols[prev], cols[idx]
        )
    return source[ty, tx]


def match_glazing_by_floor(patch, original, y, reference, area):
    # On the south tower the neighbouring building casts a dark low-level
    # shadow. Match a clean strip at the same heights without copying its edge.
    height, width = original.shape[:2]
    left, right = (int(f * width) for f in reference)
    top, bottom = area[1], area[3]
    rows = np.arange(height)
    source_rows = (
        (top + ((rows + 0.5) / height - top) % (bottom - top)) * height
    ).astype(int)
    observed = original[:, left:right].astype(float)
    healthy = observed[source_rows]

    def statistics(a):
        # A full window bay smooths individual dark windows but keeps the
        # measured change in shadow as one moves down the actual elevation.
        values = np.stack((a.mean(axis=1), a.std(axis=1)), axis=1)
        radius = 5
        smoothed = np.pad(values, ((radius, radius), (0, 0), (0, 0)), mode="edge")
        return np.lib.stride_tricks.sliding_window_view(
            smoothed, radius * 2 + 1, axis=0
        ).mean(axis=-1)

    reference_stats = statistics(observed)
    donor_stats = statistics(healthy)
    row = (y * height).astype(int).clip(0, height - 1)
    scale = np.clip(
        reference_stats[row, 1] / np.maximum(1, donor_stats[row, 1]), 0.26, 1.25
    )
    corrected = (patch - donor_stats[row, 0]) * scale + reference_stats[row, 0]
    patch[:] = corrected.round().clip(0, 255).astype(np.uint8)


def make_plate(name, rgb, owners):
    # Extend the border by three texels so mipmaps do not mix black into edges.
    coverage = owners >= 0
    for _ in range(3):
        for axis in (0, 1):
            for step in (-1, 1):
                extend = ~coverage & np.roll(coverage, step, axis)
                rgb[extend] = np.roll(rgb, step, axis)[extend]
                coverage[extend] = True
    height, width = coverage.shape
    rgba = np.ones((height, width, 4), dtype=np.float32)
    rgba[:, :, :3] = rgb[::-1] / 255
    image = bpy.data.images.new(name, width=width, height=height, alpha=False)
    image.colorspace_settings.name = "sRGB"
    image.pixels.foreach_set(rgba.ravel())
    image.pack()
    return image


def repair_facades(model, feature):
    name = feature["name"]
    if name not in REPAIRS or model.get("facadeRepairTriangles"):
        return 0
    base_angle, plans = REPAIRS[name]
    mesh = model.data
    if any(len(p.loop_indices) != 3 for p in mesh.polygons):
        raise ValueError(f"Expected source triangles: {name}")
    pos = np.empty((len(mesh.vertices), 3), dtype=np.float32)
    mesh.vertices.foreach_get("co", pos.ravel())
    # Translation cancels when framing each elevation. Use the object's assigned
    # basis too when a just-created, unparented object awaits a dependency update.
    transform = np.asarray(
        model.matrix_basis if model.parent is None else model.matrix_world
    )
    pos = pos @ transform[:3, :3].T
    loops = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loops)
    xyz = pos[loops].reshape(-1, 3, 3)
    cross = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    normals = cross / np.maximum(1e-10, np.linalg.norm(cross, axis=1)[:, None])
    layer = mesh.uv_layers.active
    uv = np.empty((len(mesh.polygons), 3, 2), dtype=np.float32)
    layer.uv.foreach_get("vector", uv.ravel())
    slots = np.array([p.material_index for p in mesh.polygons])
    pictures, cached = {}, {}
    for index in np.unique(slots):
        node = photograph(mesh.materials[index])
        if node:
            if node.image not in cached:
                cached[node.image] = texture_pixels(node.image)
            pictures[index] = cached[node.image]
    directions = sorted({plan[i] for plan in plans for i in (0, 2)})
    panels = {
        j: elevation(xyz, normals, uv, slots, pictures, base_angle + 90 * j)
        for j in directions
    }
    repaired = 0
    for target in sorted({p[0] for p in plans}):
        original, owners, screen, extent = panels[target]
        rgb = original.copy()
        height, width = owners.shape
        yy, xx = np.indices((height, width), dtype=np.float32)
        x, y = (xx + 0.5) / width, (yy + 0.5) / height
        touched = set()
        for direction, contour, donor, area, gain, *row_pattern in plans:
            if direction != target:
                continue
            mask = inside_polygon(x, y, contour) & (owners >= 0)
            if not mask.any():
                raise ValueError(f"Empty occlusion mask: {name}/{target}")
            patch = sample_donor(
                panels[donor], area, x[mask], y[mask], direction != donor, extent
            )
            if row_pattern:
                match_glazing_by_floor(patch, original, y[mask], row_pattern[0], area)
            rgb[mask] = (patch * gain).round().clip(0, 255).astype(np.uint8)
            touched.update(np.unique(owners[mask]).tolist())
        photo = make_plate(f"{name}・同棟外装補修 {target}", rgb, owners)
        clones = {}
        for face in sorted(touched):
            old = slots[face]
            if old not in clones:
                material = mesh.materials[old].copy()
                material.name = f"{name}・補修 {target}・{old}"
                photograph(material).image = photo
                clones[old] = len(mesh.materials)
                mesh.materials.append(material)
            mesh.polygons[face].material_index = clones[old]
            uv[face] = screen[face] / [width, height]
            uv[face, :, 1] = 1 - uv[face, :, 1]
        repaired += len(touched)
    layer.uv.foreach_set("vector", uv.ravel())
    mesh.update()
    model["facadeRepairTriangles"] = repaired
    model["facadeRepairSource"] = (
        "Reprojected photographed elevations of this same PLATEAU feature. See assets/OMY-FACADE-REPAIRS.md."
    )
    return repaired
