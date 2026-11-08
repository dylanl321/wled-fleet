"""Drift along a capture, and lining up a later session from two reference taps."""

from __future__ import annotations

import math

from app.mapper.geom import Vec3


def drift_fraction(index: int, count: int) -> float:
    if count <= 1:
        return 0.0
    return index / (count - 1)


def distribute_drift(points: list[Vec3], error: Vec3) -> list[Vec3]:
    """Spread a closing error along the path.

    The first point stays. The last point moves by the opposite of the error,
    which is how far a re-tap of the start reference missed it.
    """
    count = len(points)
    if count < 2:
        return list(points)
    corrected = []
    for index, point in enumerate(points):
        fraction = index / (count - 1)
        corrected.append(point - error * fraction)
    return corrected


def yaw_between(source_a: Vec3, source_b: Vec3, dest_a: Vec3, dest_b: Vec3) -> float:
    source = source_b - source_a
    dest = dest_b - dest_a
    return math.atan2(dest.x, dest.z) - math.atan2(source.x, source.z)


def apply_yaw(point: Vec3, origin: Vec3, yaw: float) -> Vec3:
    relative = point - origin
    cosine = math.cos(yaw)
    sine = math.sin(yaw)
    rotated = Vec3(
        relative.x * cosine + relative.z * sine,
        relative.y,
        -relative.x * sine + relative.z * cosine,
    )
    return rotated + origin


def align_references(
    source_a: Vec3,
    source_b: Vec3,
    dest_a: Vec3,
    dest_b: Vec3,
    points: list[Vec3],
) -> tuple[list[Vec3], float]:
    """Yaw about vertical so the source pair matches the destination pair, then translate.

    Gravity stays up. Two taps do not determine a roll, so only yaw is used.
    """
    yaw = yaw_between(source_a, source_b, dest_a, dest_b)
    spun = [apply_yaw(point, source_a, yaw) for point in points]
    shift = dest_a - apply_yaw(source_a, source_a, yaw)
    return [point + shift for point in spun], yaw
