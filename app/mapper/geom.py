"""Vector math for fixture paths. Metres, y up. No third-party numerics."""

from __future__ import annotations

import math
from dataclasses import dataclass


EPS = 1e-9


@dataclass(frozen=True)
class Vec3:
    x: float
    y: float
    z: float

    def __add__(self, other: Vec3) -> Vec3:
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: Vec3) -> Vec3:
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, scale: float) -> Vec3:
        return Vec3(self.x * scale, self.y * scale, self.z * scale)

    def __rmul__(self, scale: float) -> Vec3:
        return self.__mul__(scale)

    def dot(self, other: Vec3) -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: Vec3) -> Vec3:
        return Vec3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def length(self) -> float:
        return math.sqrt(self.dot(self))

    def normalized(self) -> Vec3:
        length = self.length()
        if length < EPS:
            return Vec3(0.0, 1.0, 0.0)
        return self * (1.0 / length)

    def as_list(self) -> list[float]:
        return [self.x, self.y, self.z]


def vec3(value: object) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("A position needs three numbers.")
    return Vec3(float(value[0]), float(value[1]), float(value[2]))


def lerp(a: Vec3, b: Vec3, t: float) -> Vec3:
    return a + (b - a) * t


def distance(a: Vec3, b: Vec3) -> float:
    return (b - a).length()


def polyline_length(points: list[Vec3]) -> float:
    total = 0.0
    for start, end in zip(points, points[1:]):
        total += distance(start, end)
    return total


def position_on_polyline(points: list[Vec3], fraction: float) -> Vec3:
    if not points:
        return Vec3(0.0, 0.0, 0.0)
    if len(points) == 1 or fraction <= 0:
        return points[0]
    if fraction >= 1:
        return points[-1]
    total = polyline_length(points)
    if total < EPS:
        return points[0]
    target = fraction * total
    walked = 0.0
    for start, end in zip(points, points[1:]):
        span = distance(start, end)
        if walked + span >= target - EPS:
            local = 0.0 if span < EPS else (target - walked) / span
            return lerp(start, end, min(max(local, 0.0), 1.0))
        walked += span
    return points[-1]


def helix_length(turns: float, radius_m: float, rise_m: float) -> float:
    """Arc length of a constant-radius helix. L = n * sqrt((2πr)^2 + (H/n)^2)."""
    if turns <= EPS:
        return abs(rise_m)
    rise_per_turn = rise_m / turns
    return turns * math.sqrt((2.0 * math.pi * radius_m) ** 2 + rise_per_turn**2)


def helix_basis(axis_start: Vec3, axis_end: Vec3, radial_hint: Vec3 | None = None) -> tuple[Vec3, Vec3, Vec3]:
    """Unit axis, plus e1 and e2 with e2 = axis × e1.

    e1 is the projection of world +X onto the plane perpendicular to the axis,
    unless a hint (the first surface tap) supplies the zero-angle direction.
    """
    axis = (axis_end - axis_start).normalized()
    e1 = _reject(Vec3(1.0, 0.0, 0.0), axis)
    if e1.length() < 1e-8:
        e1 = _reject(Vec3(0.0, 0.0, 1.0), axis)
    e1 = e1.normalized()
    if radial_hint is not None:
        hinted = _reject(radial_hint, axis)
        if hinted.length() > 1e-6:
            e1 = hinted.normalized()
    e2 = axis.cross(e1).normalized()
    return axis, e1, e2


def _reject(vector: Vec3, axis_unit: Vec3) -> Vec3:
    return vector - axis_unit * vector.dot(axis_unit)


def helix_position(
    axis_start: Vec3,
    axis_end: Vec3,
    radius_start_m: float,
    radius_end_m: float,
    turns: float,
    fraction: float,
    handedness: str = "ccw",
    radial_hint: Vec3 | None = None,
) -> Vec3:
    """P(u) = A + u(B-A) + r(u) (cos(2πnu) e1 + sin(2πnu) e2).

    Clockwise flips the sign of the sine term.
    """
    u = min(max(fraction, 0.0), 1.0)
    _axis, e1, e2 = helix_basis(axis_start, axis_end, radial_hint)
    radius = radius_start_m + (radius_end_m - radius_start_m) * u
    angle = 2.0 * math.pi * turns * u
    sine = math.sin(angle)
    if handedness == "cw":
        sine = -sine
    radial = e1 * (radius * math.cos(angle)) + e2 * (radius * sine)
    return axis_start + (axis_end - axis_start) * u + radial


def helix_arc_length(
    axis_start: Vec3,
    axis_end: Vec3,
    radius_start_m: float,
    radius_end_m: float,
    turns: float,
    handedness: str = "ccw",
    radial_hint: Vec3 | None = None,
) -> float:
    if abs(radius_end_m - radius_start_m) < 1e-9:
        rise = (axis_end - axis_start).length()
        return helix_length(turns, radius_start_m, rise)
    steps = 256
    total = 0.0
    previous = helix_position(
        axis_start, axis_end, radius_start_m, radius_end_m, turns, 0.0, handedness, radial_hint
    )
    for step in range(1, steps + 1):
        point = helix_position(
            axis_start,
            axis_end,
            radius_start_m,
            radius_end_m,
            turns,
            step / steps,
            handedness,
            radial_hint,
        )
        total += distance(previous, point)
        previous = point
    return total


def catmull_rom(p0: Vec3, p1: Vec3, p2: Vec3, p3: Vec3, t: float) -> Vec3:
    t2 = t * t
    t3 = t2 * t
    return (
        p1 * 2.0
        + (p2 - p0) * t
        + (p0 * 2.0 - p1 * 5.0 + p2 * 4.0 - p3) * t2
        + (p1 * 3.0 - p0 - p2 * 3.0 + p3) * t3
    ) * 0.5


def curve_samples(points: list[Vec3], per_span: int = 40) -> list[Vec3]:
    if len(points) < 2:
        return list(points)
    if len(points) == 2:
        return [points[0], points[1]]
    extended = [points[0], *points, points[-1]]
    samples = [points[0]]
    spans = len(points) - 1
    for index in range(spans):
        p0, p1, p2, p3 = extended[index : index + 4]
        for step in range(1, per_span + 1):
            samples.append(catmull_rom(p0, p1, p2, p3, step / per_span))
    return samples


def curve_length(points: list[Vec3]) -> float:
    samples = curve_samples(points)
    return polyline_length(samples)


def curve_position(points: list[Vec3], fraction: float) -> Vec3:
    return position_on_polyline(curve_samples(points), fraction)


def centroid(points: list[Vec3]) -> Vec3:
    if not points:
        return Vec3(0.0, 0.0, 0.0)
    scale = 1.0 / len(points)
    total = Vec3(0.0, 0.0, 0.0)
    for point in points:
        total = total + point
    return total * scale


def _covariance_apply(points: list[Vec3], center: Vec3, vector: Vec3) -> Vec3:
    result = Vec3(0.0, 0.0, 0.0)
    for point in points:
        delta = point - center
        result = result + delta * delta.dot(vector)
    return result


def fit_line(points: list[Vec3]) -> tuple[Vec3, Vec3]:
    """Least-squares axis: centroid and the principal direction."""
    center = centroid(points)
    direction = Vec3(0.0, 1.0, 0.0)
    for _ in range(48):
        updated = _covariance_apply(points, center, direction)
        if updated.length() < 1e-12:
            break
        direction = updated.normalized()
    if len(points) >= 2 and (points[-1] - points[0]).dot(direction) < 0:
        direction = direction * -1.0
    return center, direction


def project_to_line(point: Vec3, origin: Vec3, direction_unit: Vec3) -> Vec3:
    return origin + direction_unit * (point - origin).dot(direction_unit)


@dataclass(frozen=True)
class HelixFit:
    axis_start: Vec3
    axis_end: Vec3
    radius_start_m: float
    radius_end_m: float
    turns: float
    radii_m: tuple[float, ...]


def fit_helix(taps: list[Vec3]) -> HelixFit:
    """Fit an axis through the taps and read a radius from each tap.

    An upright trunk uses a vertical axis through the taps' (x, z) centroid,
    which is the base-to-top fit. A line of taps on one side of that axis has
    radius zero, so pass radiusStartMm when the phone only saw one face.
    Anything that is not clearly vertical uses a least-squares line.
    """
    if len(taps) < 2:
        raise ValueError("A helix fit needs at least two taps.")
    center, direction = _helix_axis(taps)
    start = project_to_line(taps[0], center, direction)
    end = project_to_line(taps[-1], center, direction)
    radii = tuple(distance(tap, project_to_line(tap, center, direction)) for tap in taps)
    return HelixFit(
        axis_start=start,
        axis_end=end,
        radius_start_m=radii[0],
        radius_end_m=radii[-1],
        turns=float(len(taps) - 1),
        radii_m=radii,
    )


def _helix_axis(taps: list[Vec3]) -> tuple[Vec3, Vec3]:
    vertical = max(point.y for point in taps) - min(point.y for point in taps)
    horizontal = max(
        max(point.x for point in taps) - min(point.x for point in taps),
        max(point.z for point in taps) - min(point.z for point in taps),
    )
    center = centroid(taps)
    if vertical >= max(horizontal * 2.0, 1e-6):
        direction = Vec3(0.0, 1.0 if taps[-1].y >= taps[0].y else -1.0, 0.0)
        return center, direction
    return fit_line(taps)


def fit_circle(points_xz: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Algebraic circle fit. Returns center x, center z, and radius."""
    if len(points_xz) < 3:
        raise ValueError("A circle fit needs at least three points.")
    sum_x = sum_z = sum_xx = sum_zz = sum_xz = 0.0
    sum_r = sum_rx = sum_rz = 0.0
    for x_coord, z_coord in points_xz:
        r2 = x_coord * x_coord + z_coord * z_coord
        sum_x += x_coord
        sum_z += z_coord
        sum_xx += x_coord * x_coord
        sum_zz += z_coord * z_coord
        sum_xz += x_coord * z_coord
        sum_r += r2
        sum_rx += r2 * x_coord
        sum_rz += r2 * z_coord
    count = float(len(points_xz))
    matrix = [
        [sum_xx, sum_xz, sum_x],
        [sum_xz, sum_zz, sum_z],
        [sum_x, sum_z, count],
    ]
    vector = [sum_rx, sum_rz, sum_r]
    solution = _solve3(matrix, vector)
    if solution is None:
        raise ValueError("Those points do not sit on a circle.")
    center_x = solution[0] / 2.0
    center_z = solution[1] / 2.0
    radius_sq = solution[2] + center_x * center_x + center_z * center_z
    if radius_sq <= 0:
        raise ValueError("Those points do not sit on a circle.")
    return center_x, center_z, math.sqrt(radius_sq)


def _solve3(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    rows = [row[:] + [value] for row, value in zip(matrix, vector)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(rows[row][column]))
        if abs(rows[pivot][column]) < 1e-12:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        scale = rows[column][column]
        rows[column] = [value / scale for value in rows[column]]
        for row in range(3):
            if row == column:
                continue
            factor = rows[row][column]
            rows[row] = [value - factor * base for value, base in zip(rows[row], rows[column])]
    return [rows[0][3], rows[1][3], rows[2][3]]


def nudge_toward_camera(hit: Vec3, camera: Vec3, meters: float) -> Vec3:
    """Slide a raycast hit toward the camera. Positive meters come forward."""
    ray = hit - camera
    if ray.length() < EPS:
        return hit
    return hit - ray.normalized() * meters


@dataclass(frozen=True)
class Frame:
    translation: Vec3
    yaw_radians: float

    def as_dict(self) -> dict:
        return {"translation": self.translation.as_list(), "yawRadians": self.yaw_radians}


def apply_frame(point: Vec3, frame: Frame) -> Vec3:
    shifted = point + frame.translation
    cosine = math.cos(frame.yaw_radians)
    sine = math.sin(frame.yaw_radians)
    return Vec3(
        shifted.x * cosine + shifted.z * sine,
        shifted.y,
        -shifted.x * sine + shifted.z * cosine,
    )


def normalise_points(points: list[Vec3]) -> tuple[list[Vec3], Frame]:
    """Lowest point at y=0, centroid on x=z=0, first point facing +z."""
    if not points:
        return [], Frame(Vec3(0.0, 0.0, 0.0), 0.0)
    min_y = min(point.y for point in points)
    shifted = [Vec3(point.x, point.y - min_y, point.z) for point in points]
    center = centroid(shifted)
    translation = Vec3(-center.x, -min_y, -center.z)
    recentered = [Vec3(point.x - center.x, point.y, point.z - center.z) for point in shifted]
    first = recentered[0]
    yaw = 0.0
    if math.hypot(first.x, first.z) > 1e-6:
        yaw = -math.atan2(first.x, first.z)
    frame = Frame(translation, yaw)
    return [apply_frame(point, frame) for point in points], frame
