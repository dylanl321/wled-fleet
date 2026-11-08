"""Camera-find math shared with MapperGeometry.

The phone decodes blobs and builds rays. The server uses the same frame
plan so the lights and the capture stay on the same code. Run.startIndex
is the first pixel of the run on its output. DDP adds hw.led.ins[].start.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.mapper.geom import Vec3, distance


def bit_width(count: int) -> int:
    if count <= 1:
        return 0
    bits = 0
    power = 1
    while power < count:
        power *= 2
        bits += 1
    return bits


def code_frames(indices: list[int], mode: str) -> list[dict]:
    if mode not in {"binary", "sequential"}:
        raise ValueError("Find mode must be binary or sequential.")
    frames: list[dict] = [
        {"step": 0, "role": "off", "bit": None, "lit": []},
        {"step": 1, "role": "on", "bit": None, "lit": list(indices)},
    ]
    if mode == "sequential":
        for index in indices:
            frames.append({"step": len(frames), "role": "single", "bit": None, "lit": [index]})
        return frames
    for bit in range(bit_width(len(indices))):
        lit = [index for ordinal, index in enumerate(indices) if (ordinal >> bit) & 1]
        frames.append({"step": len(frames), "role": "bit", "bit": bit, "lit": lit})
    return frames


def ddp_index(compiled_index: int, output_start: int) -> int:
    """Controller-wide pixel address.

    compiled_index already includes the run's per-output startIndex.
    output_start is hw.led.ins[].start for that output.
    """
    return output_start + compiled_index


@dataclass(frozen=True)
class ImagePoint:
    x: float
    y: float


@dataclass(frozen=True)
class BrightBlob:
    x: float
    y: float
    area: int
    peak: int


def detect_blobs(
    luminance: list[int] | bytes,
    width: int,
    height: int,
    threshold: int,
    minimum_area: int = 2,
    maximum_area: int = 8000,
) -> list[BrightBlob]:
    if width <= 0 or height <= 0 or len(luminance) < width * height:
        return []
    total = width * height
    visited = [False] * total
    blobs: list[BrightBlob] = []
    for start in range(total):
        if visited[start]:
            continue
        if luminance[start] < threshold:
            visited[start] = True
            continue
        stack = [start]
        visited[start] = True
        area = 0
        peak = 0
        sum_w = 0
        sum_x = 0
        sum_y = 0
        while stack:
            pixel = stack.pop()
            value = int(luminance[pixel])
            x = pixel % width
            y = pixel // width
            area += 1
            peak = max(peak, value)
            sum_w += value
            sum_x += x * value
            sum_y += y * value
            if x > 0:
                _push(stack, visited, luminance, pixel - 1, threshold)
            if x + 1 < width:
                _push(stack, visited, luminance, pixel + 1, threshold)
            if y > 0:
                _push(stack, visited, luminance, pixel - width, threshold)
            if y + 1 < height:
                _push(stack, visited, luminance, pixel + width, threshold)
        if area < minimum_area or area > maximum_area or sum_w <= 0:
            continue
        blobs.append(BrightBlob(sum_x / sum_w, sum_y / sum_w, area, peak))
    blobs.sort(key=lambda blob: (-blob.peak, -blob.area))
    return blobs


def _push(stack: list[int], visited: list[bool], luminance, index: int, threshold: int) -> None:
    if visited[index]:
        return
    if luminance[index] < threshold:
        visited[index] = True
        return
    visited[index] = True
    stack.append(index)


def downsample_factor(width: int, height: int, longest: int = 960) -> int:
    big = max(width, height)
    if big <= longest or longest < 1:
        return 1
    return max(1, math.ceil(big / longest))


def downsample_max(luminance: list[int] | bytes, width: int, height: int, factor: int) -> tuple[list[int], int, int]:
    if factor <= 1:
        return [int(value) for value in luminance[: width * height]], width, height
    out_w = max(1, width // factor)
    out_h = max(1, height // factor)
    output = [0] * (out_w * out_h)
    for y in range(out_h):
        for x in range(out_w):
            peak = 0
            for dy in range(factor):
                sy = y * factor + dy
                if sy >= height:
                    break
                row = sy * width
                for dx in range(factor):
                    sx = x * factor + dx
                    if sx >= width:
                        break
                    peak = max(peak, int(luminance[row + sx]))
            output[y * out_w + x] = peak
    return output, out_w, out_h


def full_resolution(point: ImagePoint, scale: int) -> ImagePoint:
    if scale <= 1:
        return point
    shift = (scale - 1) / 2
    return ImagePoint(point.x * scale + shift, point.y * scale + shift)


@dataclass(frozen=True)
class RoomLight:
    mean: float
    percentile90: float
    dark: bool


def room_brightness(luminance: list[int] | bytes) -> RoomLight:
    if not luminance:
        return RoomLight(255, 255, False)
    total = 0
    hist = [0] * 256
    for value in luminance:
        level = int(value)
        if level < 0:
            level = 0
        if level > 255:
            level = 255
        total += level
        hist[level] += 1
    mean = total / len(luminance)
    target = math.ceil(len(luminance) * 0.90)
    seen = 0
    percentile = 255
    for level, count in enumerate(hist):
        seen += count
        if seen >= target:
            percentile = level
            break
    return RoomLight(mean, float(percentile), mean < 36 and percentile < 80)


def threshold_from_reference(off: list[int] | bytes, on: list[int] | bytes) -> int:
    off_peak = max((int(value) for value in off), default=0)
    on_peak = max((int(value) for value in on), default=0)
    if on_peak <= off_peak + 8:
        return min(250, off_peak + 12)
    mid = (off_peak + on_peak) // 2
    return min(250, max(off_peak + 12, mid))


def _nearest(point: ImagePoint, points: list[ImagePoint], radius: float) -> int | None:
    best = None
    best_d = radius
    for index, other in enumerate(points):
        delta = math.hypot(point.x - other.x, point.y - other.y)
        if delta <= best_d:
            best_d = delta
            best = index
    return best


@dataclass(frozen=True)
class DecodedBlob:
    index: int | None
    x: float
    y: float
    stable: bool


def decode_blobs(frames: list[dict], observations: list[list[ImagePoint]], match_radius: float) -> list[DecodedBlob]:
    if len(observations) != len(frames):
        raise ValueError("Every code frame needs one observation list.")
    by_step = {int(frame["step"]): observations[position] for position, frame in enumerate(frames)}
    off = by_step.get(0, [])
    on = by_step.get(1, [])
    candidates = [point for point in on if _nearest(point, off, match_radius) is None]
    bit_frames = sorted(
        (frame for frame in frames if frame.get("role") == "bit"),
        key=lambda frame: int(frame.get("bit") or 0),
    )
    indices = list(frames[1]["lit"]) if len(frames) > 1 else []
    decoded: list[DecodedBlob] = []
    singles = [frame for frame in frames if frame.get("role") == "single"]
    if bit_frames:
        for point in candidates:
            ordinal = 0
            for frame in bit_frames:
                seen = _nearest(point, by_step.get(int(frame["step"]), []), match_radius) is not None
                if seen:
                    ordinal |= 1 << int(frame.get("bit") or 0)
            index = indices[ordinal] if 0 <= ordinal < len(indices) else None
            decoded.append(DecodedBlob(index, point.x, point.y, True))
    elif not singles and len(indices) == 1:
        for point in candidates:
            decoded.append(DecodedBlob(indices[0], point.x, point.y, True))
    else:
        claimed: set[int] = set()
        for frame in frames:
            if frame.get("role") != "single":
                continue
            fresh = [point for point in by_step.get(int(frame["step"]), []) if _nearest(point, off, match_radius) is None]
            if not fresh:
                continue
            pool = [(index, point) for index, point in enumerate(candidates) if index not in claimed]
            choice = fresh[0]
            if pool:
                best = min(pool, key=lambda item: math.hypot(item[1].x - choice.x, item[1].y - choice.y))
                if math.hypot(best[1].x - choice.x, best[1].y - choice.y) <= match_radius:
                    claimed.add(best[0])
                    choice = best[1]
            lit = frame.get("lit") or []
            index = int(lit[0]) if lit else None
            decoded.append(DecodedBlob(index, choice.x, choice.y, True))
    counts: dict[int, int] = {}
    for blob in decoded:
        if blob.index is not None:
            counts[blob.index] = counts.get(blob.index, 0) + 1
    unique: list[DecodedBlob] = []
    for blob in decoded:
        if blob.index is not None and counts[blob.index] > 1:
            unique.append(DecodedBlob(None, blob.x, blob.y, True))
        else:
            unique.append(blob)
    return unique


@dataclass(frozen=True)
class Ray:
    origin: Vec3
    direction: Vec3


@dataclass(frozen=True)
class CameraIntrinsics:
    fx: float
    fy: float
    cx: float
    cy: float


@dataclass(frozen=True)
class CameraPose:
    right: Vec3
    up: Vec3
    back: Vec3
    origin: Vec3


def ray_from_pixel(u: float, v: float, intrinsics: CameraIntrinsics, pose: CameraPose) -> Ray:
    x = 0.0 if intrinsics.fx == 0 else (u - intrinsics.cx) / intrinsics.fx
    y = 0.0 if intrinsics.fy == 0 else (v - intrinsics.cy) / intrinsics.fy
    local = Vec3(x, -y, -1).normalized()
    world = pose.right * local.x + pose.up * local.y + pose.back * local.z
    return Ray(pose.origin, world.normalized())


@dataclass(frozen=True)
class Triangulation:
    position: Vec3
    gap: float
    along_a: float
    along_b: float


def triangulate(first: Ray, second: Ray) -> Triangulation | None:
    d1 = first.direction.normalized()
    d2 = second.direction.normalized()
    gap0 = first.origin - second.origin
    aa = d1.dot(d1)
    bb = d1.dot(d2)
    cc = d2.dot(d2)
    dd = d1.dot(gap0)
    ee = d2.dot(gap0)
    denom = aa * cc - bb * bb
    if abs(denom) < 1e-10:
        return None
    along_a = (bb * ee - cc * dd) / denom
    along_b = (aa * ee - bb * dd) / denom
    if along_a <= 1e-4 or along_b <= 1e-4:
        return None
    point_a = first.origin + d1 * along_a
    point_b = second.origin + d2 * along_b
    return Triangulation((point_a + point_b) * 0.5, distance(point_a, point_b), along_a, along_b)


@dataclass(frozen=True)
class SegmentHit:
    position: Vec3
    segment: str
    distance: float
    along: float


def closest_on_segment(ray: Ray, start: Vec3, end: Vec3) -> tuple[Vec3, float, float, float] | None:
    direction = ray.direction.normalized()
    span = end - start
    length2 = span.dot(span)
    if length2 < 1e-12:
        along = (start - ray.origin).dot(direction)
        if along <= 1e-4:
            return None
        closest = ray.origin + direction * along
        return start, distance(start, closest), along, 0.0
    gap0 = ray.origin - start
    aa = direction.dot(direction)
    bb = direction.dot(span)
    cc = length2
    dd = direction.dot(gap0)
    ee = span.dot(gap0)
    denom = aa * cc - bb * bb
    if abs(denom) < 1e-12:
        fraction = min(max(ee / cc, 0.0), 1.0)
        along = dd
    else:
        along = (bb * ee - cc * dd) / denom
        fraction = (aa * ee - bb * dd) / denom
    if fraction < 0 or fraction > 1:
        fraction = min(max(fraction, 0.0), 1.0)
        point = start + span * fraction
        along = (point - ray.origin).dot(direction)
    if along <= 1e-4:
        return None
    point = start + span * fraction
    closest = ray.origin + direction * along
    return point, distance(point, closest), along, fraction


def refine_on_model(ray: Ray, samples: list[tuple[str, Vec3]], max_distance: float) -> SegmentHit | None:
    best: SegmentHit | None = None
    for index, (segment, point) in enumerate(samples):
        if index + 1 >= len(samples) or samples[index + 1][0] != segment:
            along = (point - ray.origin).dot(ray.direction.normalized())
            if along <= 1e-4:
                continue
            closest = ray.origin + ray.direction.normalized() * along
            gap = distance(point, closest)
            if gap > max_distance:
                continue
            hit = SegmentHit(point, segment, gap, along)
        else:
            found = closest_on_segment(ray, point, samples[index + 1][1])
            if found is None or found[1] > max_distance:
                continue
            hit = SegmentHit(found[0], segment, found[1], found[2])
        if best is None or hit.distance < best.distance:
            best = hit
    return best


@dataclass(frozen=True)
class Assessment:
    index: int
    position: Vec3 | None
    confidence: float
    method: str | None
    viewpoints: int
    outlier: bool
    missing: bool
    occluded: bool
    segment: str


def assess_pixels(
    model: list[tuple[int, Vec3, str]],
    viewpoints: list[list[tuple[int, Ray]]],
    outlier_metres: float = 0.15,
    ray_gap_metres: float = 0.08,
    finished: bool = False,
) -> list[Assessment]:
    samples = [(segment, position) for _index, position, segment in model]
    results: list[Assessment] = []
    for index, position, segment in model:
        rays = [ray for view in viewpoints for seen_index, ray in view if seen_index == index]
        if not rays:
            hidden = bool(viewpoints) and not finished
            results.append(
                Assessment(index, None, 0, None, 0, False, bool(viewpoints) and finished, hidden, segment)
            )
            continue
        triangulated = _best_triangulation(rays)
        refined = refine_on_model(rays[0], samples, 0.25) if len(rays) == 1 or triangulated is None else None
        if triangulated is not None and triangulated.gap <= ray_gap_metres:
            measured = triangulated.position
            method = "triangulated"
            confidence = max(0.2, min(1.0, 1 - triangulated.gap / ray_gap_metres))
            far = distance(measured, position) > outlier_metres
            results.append(Assessment(index, measured, confidence, method, len(rays), far, False, False, segment))
            continue
        if refined is not None:
            far = distance(refined.position, position) > outlier_metres or refined.distance > 0.05
            confidence = 0.2 if far else max(0.35, min(1.0, 1 - refined.distance / 0.05))
            results.append(
                Assessment(index, refined.position, confidence, "segment", len(rays), far, False, False, segment)
            )
            continue
        if triangulated is not None:
            results.append(
                Assessment(
                    index,
                    triangulated.position,
                    0.2,
                    "triangulated",
                    len(rays),
                    True,
                    False,
                    False,
                    segment,
                )
            )
            continue
        hidden = not finished
        results.append(Assessment(index, None, 0, None, len(rays), finished, finished, hidden, segment))
    return results


def _best_triangulation(rays: list[Ray]) -> Triangulation | None:
    best: Triangulation | None = None
    for left in range(len(rays)):
        for right in range(left + 1, len(rays)):
            found = triangulate(rays[left], rays[right])
            if found is None:
                continue
            if best is None or found.gap < best.gap:
                best = found
    return best
