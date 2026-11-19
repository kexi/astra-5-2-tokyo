"""Round metric ENU vertices to GPU floats while retaining the winding of thin roofs."""

from itertools import product

import numpy as np


def area_vectors(triangles):
    points = triangles.astype(np.float64, copy=False)
    return np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])


def upward(vectors):
    return vectors[:, 2] > 0.45 * np.linalg.norm(vectors, axis=1)


def export_positions(original, ortho):
    rounded = original.astype(np.float32)
    if not np.any(ortho):
        return rounded
    roofs = np.flatnonzero(ortho)
    invalid = roofs[~upward(area_vectors(rounded[roofs]))]
    if not len(invalid):
        return rounded
    exact_normals = area_vectors(original)
    if not np.all(upward(exact_normals[invalid])):
        raise ValueError("The native roof already faces down or is degenerate")
    before = area_vectors(rounded)
    for index in invalid:
        if upward(before[index : index + 1])[0]:
            continue
        options = []
        for corner in range(3):
            original_point = original[index, corner]
            occurrences = np.all(original == original_point, axis=2)
            neighbors = np.any(occurrences, axis=1)
            close_roofs = ortho[neighbors]
            current = rounded[neighbors]
            shared = occurrences[neighbors]
            point = rounded[index, corner]
            lower = np.nextafter(point, np.float32(-np.inf))
            upper = np.nextafter(point, np.float32(np.inf))
            for candidate in product(*zip(lower, point, upper, strict=True)):
                adjacent = current.copy()
                adjacent[shared] = candidate
                after = area_vectors(adjacent)
                repaired = upward(after)
                if not np.all(repaired[close_roofs]):
                    continue
                # Preserve every neighboring face whose original winding survived rounding.
                ref = exact_normals[neighbors]
                correct = np.sum(before[neighbors] * ref, axis=1) > 0
                if np.any(np.sum(after[correct] * ref[correct], axis=1) <= 0):
                    continue
                delta = np.linalg.norm(original_point - candidate)
                options.append((delta, corner, candidate))
        if not options:
            raise ValueError(f"Cannot preserve a thin roof at triangle {index}")
        _, corner, best = min(options)
        rounded[np.all(original == original[index, corner], axis=2)] = best
        before = area_vectors(rounded)
    return rounded
