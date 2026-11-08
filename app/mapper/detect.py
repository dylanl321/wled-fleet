"""Binary pixel identification and camera rays.

The phone finds blobs. These functions turn frame bits and camera geometry
into a pixel index and a 3D point. They do not need a camera or a board.
"""

from __future__ import annotations

import math

from app.mapper.geom import EPS, Vec3


def frame_count(pixel_count: int) -> int:
    if pixel_count <= 1:
        return 1
    return math.ceil(math.log2(pixel_count))


def lights_for_frame(pixel_count: int, frame: int) -> list[int]:
    return [index for index in range(pixel_count) if (index >> frame) & 1]


def detection_plan(pixel_count: int, mode: str = "binary") -> list[list[int]]:
    if mode == "single":
        return [[index] for index in range(pixel_count)]
    frames = frame_count(pixel_count)
    return [lights_for_frame(pixel_count, frame) for frame in range(frames)]


def decode_bits(seen: list[bool]) -> int:
    value = 0
    for bit, on in enumerate(seen):
        if on:
            value |= 1 << bit
    return value


def is_dark(average_luma: float, limit: float = 40.0) -> bool:
    return average_luma <= limit


def ray_from_pixel(u: float, v: float, intrinsics: dict, pose: list[list[float]]) -> tuple[Vec3, Vec3]:
    """Ray in world space. Pose is camera-to-world, column vectors, camera looks down -Z."""
    fx = float(intrinsics["fx"])
    fy = float(intrinsics["fy"])
    cx = float(intrinsics["cx"])
    cy = float(intrinsics["cy"])
    local = Vec3((u - cx) / fx, (v - cy) / fy, -1.0).normalized()
    origin = Vec3(pose[0][3], pose[1][3], pose[2][3])
    direction = _rotate(pose, local).normalized()
    return origin, direction


def _rotate(pose: list[list[float]], vector: Vec3) -> Vec3:
    return Vec3(
        pose[0][0] * vector.x + pose[0][1] * vector.y + pose[0][2] * vector.z,
        pose[1][0] * vector.x + pose[1][1] * vector.y + pose[1][2] * vector.z,
        pose[2][0] * vector.x + pose[2][1] * vector.y + pose[2][2] * vector.z,
    )


def intersect_ray_segment(origin: Vec3, direction: Vec3, start: Vec3, end: Vec3) -> tuple[float, Vec3] | None:
    """Closest point on the segment to the ray. Returns distance and the point."""
    segment = end - start
    # Solve origin + t direction ≈ start + s segment, t >= 0, s in [0, 1].
    a = direction.dot(direction)
    b = direction.dot(segment)
    c = segment.dot(segment)
    d = direction.dot(origin - start)
    e = segment.dot(origin - start)
    denom = a * c - b * b
    if abs(denom) < EPS or c < EPS:
        return None
    t = (b * e - c * d) / denom
    s = (a * e - b * d) / denom
    if t < 0:
        return None
    s = min(max(s, 0.0), 1.0)
    point = start + segment * s
    closest = origin + direction * t
    return distance(closest, point), point


def distance(a: Vec3, b: Vec3) -> float:
    return (a - b).length()


def refine_with_model(origin: Vec3, direction: Vec3, segments: list[tuple[Vec3, Vec3]], limit: float = 0.05) -> Vec3 | None:
    best: tuple[float, Vec3] | None = None
    for start, end in segments:
        hit = intersect_ray_segment(origin, direction, start, end)
        if hit is None:
            continue
        if hit[0] > limit:
            continue
        if best is None or hit[0] < best[0]:
            best = hit
    return None if best is None else best[1]


def triangulate(origin_a: Vec3, direction_a: Vec3, origin_b: Vec3, direction_b: Vec3) -> Vec3:
    """Midpoint of the shortest bridge between two rays."""
    w0 = origin_a - origin_b
    a = direction_a.dot(direction_a)
    b = direction_a.dot(direction_b)
    c = direction_b.dot(direction_b)
    d = direction_a.dot(w0)
    e = direction_b.dot(w0)
    denom = a * c - b * b
    if abs(denom) < EPS:
        return origin_a
    t = (b * e - c * d) / denom
    s = (a * e - b * d) / denom
    point_a = origin_a + direction_a * t
    point_b = origin_b + direction_b * s
    return (point_a + point_b) * 0.5


def missing_indices(pixel_count: int, seen: set[int]) -> list[int]:
    return [index for index in range(pixel_count) if index not in seen]


def decode_stationary(frames: list[list[tuple[float, float]]], join: float = 8.0) -> list[dict]:
    """Group blobs that stay in the same image spot, then read each group's bits.

    The phone should be still. A pixel that never lights is absent from the result.
    """
    clusters: list[dict] = []
    total = len(frames)
    for frame_index, blobs in enumerate(frames):
        for u, v in blobs:
            best = None
            best_distance = join
            for cluster in clusters:
                gap = math.hypot(cluster["u"] - u, cluster["v"] - v)
                if gap <= best_distance:
                    best = cluster
                    best_distance = gap
            if best is None:
                best = {"u": u, "v": v, "count": 0, "bits": [False] * total}
                clusters.append(best)
            count = best["count"]
            best["u"] = (best["u"] * count + u) / (count + 1)
            best["v"] = (best["v"] * count + v) / (count + 1)
            best["count"] = count + 1
            best["bits"][frame_index] = True
    return [{"index": decode_bits(cluster["bits"]), "u": cluster["u"], "v": cluster["v"]} for cluster in clusters]


def brightest_blobs(luma: list[float], width: int, height: int, threshold: float, limit: int, radius: int = 2) -> list[tuple[float, float]]:
    """Brightest-first scan. Each hit blanks a small neighbourhood so one LED is one blob."""
    if width < 1 or height < 1 or len(luma) < width * height:
        return []
    values = list(luma[: width * height])
    found: list[tuple[float, float]] = []
    for _ in range(limit):
        index = max(range(len(values)), key=values.__getitem__)
        if values[index] < threshold:
            break
        column = index % width
        row = index // width
        found.append((float(column), float(row)))
        for y in range(max(0, row - radius), min(height, row + radius + 1)):
            for x in range(max(0, column - radius), min(width, column + radius + 1)):
                values[y * width + x] = 0.0
    return found
