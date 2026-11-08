"""Drift closure, multi-session alignment, and branch coordinates.

Metres, y up. Gravity fixes pitch and roll, so two labelled marks determine a
yaw about Y plus a translation. Three or more marks use Kabsch, which can
recover a full rotation when a tap is noisy. No third-party numerics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.mapper.geom import EPS, Frame, Vec3, centroid, distance


@dataclass(frozen=True)
class Rigid:
    """p' = R p + t. rotation is row-major 3x3. version is the alignment document version."""

    version: int
    method: str
    rotation: tuple[float, ...]
    translation: Vec3
    yaw_radians: float
    residual_m: float
    reference_ids: tuple[str, ...] = ()

    def apply(self, point: Vec3) -> Vec3:
        return apply_rotation(self.rotation, point) + self.translation

    def as_dict(self) -> dict:
        body = {
            "version": self.version,
            "method": self.method,
            "rotation": [float(value) for value in self.rotation],
            "translation": self.translation.as_list(),
            "yawRadians": self.yaw_radians,
            "residualM": self.residual_m,
        }
        if self.reference_ids:
            body["referenceIds"] = list(self.reference_ids)
        return body


@dataclass(frozen=True)
class DriftResult:
    corrected: tuple[Vec3, ...]
    error: Vec3
    error_m: float
    weights: tuple[float, ...]


def yaw_matrix(yaw: float) -> tuple[float, ...]:
    cosine = math.cos(yaw)
    sine = math.sin(yaw)
    return (
        cosine, 0.0, sine,
        0.0, 1.0, 0.0,
        -sine, 0.0, cosine,
    )


def apply_rotation(rotation: tuple[float, ...] | list[float], point: Vec3) -> Vec3:
    r = rotation
    return Vec3(
        r[0] * point.x + r[1] * point.y + r[2] * point.z,
        r[3] * point.x + r[4] * point.y + r[5] * point.z,
        r[6] * point.x + r[7] * point.y + r[8] * point.z,
    )


def frame_as_rigid(frame: Frame, method: str = "worldMap", reference_ids: tuple[str, ...] = ()) -> Rigid:
    """The capture frame translates, then yaws. Store the same map as R p + t."""
    rotation = yaw_matrix(frame.yaw_radians)
    translation = apply_rotation(rotation, frame.translation)
    return Rigid(1, method, rotation, translation, frame.yaw_radians, 0.0, reference_ids)


def distribute_drift(points: list[Vec3], retap: Vec3, reference_index: int = 0) -> DriftResult:
    """Spread the closure error along the path in proportion to distance walked.

    The reference point stays where it was. A point halfway along the walk from
    the reference, out to the re-tap, receives half of (reference - retap).
    Points placed before the reference are left alone.
    """
    if reference_index < 0 or reference_index >= len(points):
        raise ValueError("The reference tap is not on this path.")
    reference = points[reference_index]
    error = reference - retap
    walked = 0.0
    distances = [0.0] * len(points)
    for index in range(reference_index + 1, len(points)):
        walked += distance(points[index - 1], points[index])
        distances[index] = walked
    total = walked + distance(points[-1], retap)
    corrected: list[Vec3] = []
    weights: list[float] = []
    for index, point in enumerate(points):
        if index < reference_index or total < EPS:
            weight = 0.0
        else:
            weight = distances[index] / total
        weights.append(weight)
        corrected.append(point + error * weight)
    return DriftResult(tuple(corrected), error, error.length(), tuple(weights))


def align_points(
    source: list[Vec3],
    target: list[Vec3],
    reference_ids: list[str] | None = None,
) -> Rigid:
    """Map session taps (source) onto saved marks (target).

    Two marks: yaw about gravity plus a translation. Three or more: Kabsch.
    """
    if len(source) < 2 or len(source) != len(target):
        raise ValueError("Alignment needs two or three matching marks.")
    ids = tuple(reference_ids or ())
    if len(source) == 2:
        rotation, translation, yaw = _yaw_translation(source, target)
        method = "yawTranslation"
    else:
        rotation, translation = _kabsch(source, target)
        yaw = _yaw_of(rotation)
        method = "kabsch"
    residual = _residual(source, target, rotation, translation)
    return Rigid(1, method, tuple(rotation), translation, yaw, residual, ids)


def outward_parameters(
    points: list[Vec3],
    role: str,
    trunk_points: list[Vec3],
) -> list[tuple[float, float]]:
    """(u outward from the root, height above the group's lowest point).

    The trunk's root is its lower end. A branch's root is the end nearer the
    trunk, so a limb wired from the tip still chases outward. u is 0 at the
    root and 1 at the far end.
    """
    if not points:
        return []
    if len(points) == 1:
        return [(0.0, 0.0)]
    root_first = _root_is_first(points, role, trunk_points)
    cumulative = [0.0]
    for start, end in zip(points, points[1:]):
        cumulative.append(cumulative[-1] + distance(start, end))
    total = cumulative[-1]
    lowest = min(point.y for point in points)
    pairs: list[tuple[float, float]] = []
    for point, walked in zip(points, cumulative):
        wire = 0.0 if total < EPS else walked / total
        outward = wire if root_first else 1.0 - wire
        pairs.append((outward, point.y - lowest))
    return pairs


def derive_references(waypoints: list[dict]) -> list[dict]:
    """Pick two or three labelled marks so a later session can line itself up.

    Prefer labels. When a path has room, keep a middle mark as the third so
    Kabsch has something to hold onto.
    """
    usable = [item for item in waypoints if isinstance(item, dict) and isinstance(item.get("id"), str)]
    if len(usable) < 2:
        return []
    labelled = [
        item
        for item in usable
        if isinstance(item.get("label"), str) and str(item.get("label")).strip()
    ]
    pool = labelled if len(labelled) >= 2 else usable
    if len(pool) > 3:
        pool = [pool[0], pool[len(pool) // 2], pool[-1]]
    elif len(pool) == 2 and len(usable) >= 3:
        middle = usable[len(usable) // 2]
        chosen = {pool[0].get("id"), pool[1].get("id")}
        if middle.get("id") not in chosen:
            pool = [pool[0], middle, pool[1]]
    references = []
    for index, waypoint in enumerate(pool[:3]):
        raw_label = waypoint.get("label")
        label = raw_label.strip() if isinstance(raw_label, str) and raw_label.strip() else f"Mark {index + 1}"
        waypoint_id = str(waypoint["id"])
        reference_id = f"ref-{waypoint_id}"
        if len(reference_id) > 80:
            reference_id = waypoint_id[:80]
        references.append(
            {
                "id": reference_id,
                "label": label[:80],
                "position": list(waypoint.get("position") or [0, 0, 0]),
                "waypointId": waypoint_id,
            }
        )
    return references


def _yaw_translation(source: list[Vec3], target: list[Vec3]) -> tuple[list[float], Vec3, float]:
    source_span = source[1] - source[0]
    target_span = target[1] - target[0]
    if math.hypot(source_span.x, source_span.z) < 1e-6 or math.hypot(target_span.x, target_span.z) < 1e-6:
        yaw = 0.0
    else:
        yaw = math.atan2(target_span.x, target_span.z) - math.atan2(source_span.x, source_span.z)
        yaw = math.atan2(math.sin(yaw), math.cos(yaw))
    rotation = list(yaw_matrix(yaw))
    translation = centroid(target) - apply_rotation(rotation, centroid(source))
    return rotation, translation, yaw


def _yaw_of(rotation: list[float] | tuple[float, ...]) -> float:
    return math.atan2(rotation[2], rotation[0])


def _residual(source: list[Vec3], target: list[Vec3], rotation: list[float] | tuple[float, ...], translation: Vec3) -> float:
    if not source:
        return 0.0
    total = 0.0
    for point, goal in zip(source, target):
        total += distance(apply_rotation(rotation, point) + translation, goal) ** 2
    residual = math.sqrt(total / len(source))
    if residual < 1e-12:
        return 0.0
    return residual


def _root_is_first(points: list[Vec3], role: str, trunk_points: list[Vec3]) -> bool:
    first, last = points[0], points[-1]
    if role == "branch" and trunk_points:
        near_first = min(distance(first, trunk) for trunk in trunk_points)
        near_last = min(distance(last, trunk) for trunk in trunk_points)
        if abs(near_first - near_last) > 1e-6:
            return near_first < near_last
    if abs(first.y - last.y) <= 1e-6:
        return True
    return first.y < last.y


def _kabsch(source: list[Vec3], target: list[Vec3]) -> tuple[list[float], Vec3]:
    source_center = centroid(source)
    target_center = centroid(target)
    covariance = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    for point, goal in zip(source, target):
        delta_s = point - source_center
        delta_t = goal - target_center
        source_coords = (delta_s.x, delta_s.y, delta_s.z)
        target_coords = (delta_t.x, delta_t.y, delta_t.z)
        for row in range(3):
            for column in range(3):
                covariance[row][column] += source_coords[row] * target_coords[column]
    left, _singular, vt = _svd3(covariance)
    rotation = _matmul(_transpose(vt), _transpose(left))
    if _det(rotation) < 0:
        vt = [row[:] for row in vt]
        vt[2] = [-value for value in vt[2]]
        rotation = _matmul(_transpose(vt), _transpose(left))
    flat = [value for row in rotation for value in row]
    translation = target_center - apply_rotation(flat, source_center)
    return flat, translation


def _svd3(matrix: list[list[float]]) -> tuple[list[list[float]], list[float], list[list[float]]]:
    """Thin SVD of a 3x3. Returns U, singular values, and V transposed. M = U diag(S) Vt."""
    transpose = _transpose(matrix)
    eigenvalues, eigenvectors = _jacobi_symmetric(_matmul(transpose, matrix))
    singular = [math.sqrt(max(value, 0.0)) for value in eigenvalues]
    columns: list[list[float] | None] = []
    for index in range(3):
        axis = [eigenvectors[row][index] for row in range(3)]
        projected = _matvec(matrix, axis)
        if singular[index] > 1e-12:
            columns.append([value / singular[index] for value in projected])
        else:
            columns.append(None)
    filled = _complete_basis(columns)
    left = _columns_to_rows(_orthonormalize(filled))
    return left, singular, _transpose(eigenvectors)


def _jacobi_symmetric(matrix: list[list[float]]) -> tuple[list[float], list[list[float]]]:
    """Eigenvalues, descending, and eigenvectors as columns of a symmetric 3x3."""
    working = [row[:] for row in matrix]
    basis = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for _ in range(32):
        pivot_row, pivot_column = 0, 1
        largest = abs(working[0][1])
        for row, column in ((0, 2), (1, 2)):
            magnitude = abs(working[row][column])
            if magnitude > largest:
                largest = magnitude
                pivot_row, pivot_column = row, column
        if largest < 1e-15:
            break
        diagonal_row = working[pivot_row][pivot_row]
        diagonal_column = working[pivot_column][pivot_column]
        off = working[pivot_row][pivot_column]
        if abs(off) < 1e-20:
            cosine, sine = 1.0, 0.0
        else:
            tau = (diagonal_column - diagonal_row) / (2.0 * off)
            if tau >= 0:
                tangent = 1.0 / (tau + math.sqrt(1.0 + tau * tau))
            else:
                tangent = -1.0 / (-tau + math.sqrt(1.0 + tau * tau))
            cosine = 1.0 / math.sqrt(1.0 + tangent * tangent)
            sine = tangent * cosine
        for row in range(3):
            if row == pivot_row or row == pivot_column:
                continue
            row_pivot = working[row][pivot_row]
            row_column = working[row][pivot_column]
            working[row][pivot_row] = working[pivot_row][row] = cosine * row_pivot - sine * row_column
            working[row][pivot_column] = working[pivot_column][row] = sine * row_pivot + cosine * row_column
        working[pivot_row][pivot_row] = (
            cosine * cosine * diagonal_row - 2.0 * sine * cosine * off + sine * sine * diagonal_column
        )
        working[pivot_column][pivot_column] = (
            sine * sine * diagonal_row + 2.0 * sine * cosine * off + cosine * cosine * diagonal_column
        )
        working[pivot_row][pivot_column] = working[pivot_column][pivot_row] = 0.0
        for row in range(3):
            basis_row = basis[row][pivot_row]
            basis_column = basis[row][pivot_column]
            basis[row][pivot_row] = cosine * basis_row - sine * basis_column
            basis[row][pivot_column] = sine * basis_row + cosine * basis_column
    values = [working[0][0], working[1][1], working[2][2]]
    order = sorted(range(3), key=lambda index: values[index], reverse=True)
    values = [values[index] for index in order]
    basis = [[basis[row][index] for index in order] for row in range(3)]
    return values, basis


def _complete_basis(columns: list[list[float] | None]) -> list[list[float]]:
    result: list[list[float] | None] = []
    for column in columns:
        result.append(_unit(column) if column is not None else None)
    for index, column in enumerate(result):
        if column is not None:
            continue
        known = [item for item in result if item is not None]
        if not known:
            result[index] = [1.0, 0.0, 0.0]
        elif len(known) == 1:
            helper = [1.0, 0.0, 0.0] if abs(known[0][0]) < 0.9 else [0.0, 1.0, 0.0]
            result[index] = _unit(_cross(known[0], helper))
        else:
            result[index] = _unit(_cross(known[0], known[1]))
    return [column if column is not None else [1.0, 0.0, 0.0] for column in result]


def _orthonormalize(columns: list[list[float]]) -> list[list[float]]:
    kept: list[list[float]] = []
    for column in columns:
        vector = column[:]
        for previous in kept:
            scale = sum(left * right for left, right in zip(vector, previous))
            vector = [value - scale * previous[index] for index, value in enumerate(vector)]
        kept.append(_unit(vector))
    return kept


def _unit(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in vector))
    if length < 1e-15:
        return [1.0, 0.0, 0.0]
    return [value / length for value in vector]


def _cross(left: list[float], right: list[float]) -> list[float]:
    return [
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    ]


def _transpose(matrix: list[list[float]]) -> list[list[float]]:
    return [list(column) for column in zip(*matrix)]


def _matmul(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    product = [[0.0, 0.0, 0.0] for _ in range(3)]
    for row in range(3):
        for column in range(3):
            product[row][column] = sum(left[row][inner] * right[inner][column] for inner in range(3))
    return product


def _matvec(matrix: list[list[float]], vector: list[float]) -> list[float]:
    return [sum(matrix[row][column] * vector[column] for column in range(3)) for row in range(3)]


def _columns_to_rows(columns: list[list[float]]) -> list[list[float]]:
    return [[columns[column][row] for column in range(3)] for row in range(3)]


def _det(matrix: list[list[float]]) -> float:
    return (
        matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
        - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
        + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
    )
