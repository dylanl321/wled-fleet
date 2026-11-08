"""Turn a fixture document into one row per pixel."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from app.mapper.geom import (
    EPS,
    Vec3,
    curve_length,
    curve_position,
    distance,
    fit_helix,
    helix_arc_length,
    helix_position,
    polyline_length,
    position_on_polyline,
    vec3,
)


class CompileError(ValueError):
    pass


@dataclass(frozen=True)
class Pixel:
    fixture: str
    controller: str
    output: int
    index: int
    x: float
    y: float
    z: float
    segment: str
    u: float

    def as_dict(self) -> dict:
        return {
            "fixture": self.fixture,
            "controller": self.controller,
            "output": self.output,
            "index": self.index,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "segment": self.segment,
            "u": self.u,
        }


def waypoint_index(fixture: dict) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for waypoint in fixture.get("waypoints") or []:
        if isinstance(waypoint, dict) and isinstance(waypoint.get("id"), str):
            found[waypoint["id"]] = waypoint
    return found


def points_for(segment: dict, waypoints: dict[str, dict]) -> list[Vec3]:
    points: list[Vec3] = []
    for waypoint_id in segment.get("waypointIds") or []:
        waypoint = waypoints.get(waypoint_id)
        if waypoint is None:
            raise CompileError(f"Segment {segment.get('id')} references unknown waypoint {waypoint_id}.")
        points.append(vec3(waypoint.get("position")))
    if len(points) < 2:
        raise CompileError(f"Segment {segment.get('id')} needs at least two waypoints.")
    return points


def _helix_args(segment: dict, points: list[Vec3]) -> tuple[Vec3, Vec3, float, float, float, str, Vec3 | None]:
    params = segment.get("params") if isinstance(segment.get("params"), dict) else {}
    handedness = params.get("handedness") if params.get("handedness") in {"cw", "ccw"} else "ccw"
    radius_start = float(params["radiusStartMm"]) / 1000.0 if "radiusStartMm" in params else None
    radius_end = float(params["radiusEndMm"]) / 1000.0 if "radiusEndMm" in params else radius_start
    inward = vec3(params["inward"]) if isinstance(params.get("inward"), list) and len(params["inward"]) == 3 else None
    # Two stored points are the axis ends. Three or more are surface taps, one per turn.
    if len(points) >= 3:
        fitted = fit_helix(points, radius_m=radius_start, inward=inward)
        hint = points[0] - fitted.axis_start
        return (
            fitted.axis_start,
            fitted.axis_end,
            radius_start if radius_start is not None else fitted.radius_start_m,
            radius_end if radius_end is not None else fitted.radius_end_m,
            fitted.turns,
            handedness,
            hint,
        )
    if radius_start is None:
        fitted = fit_helix(points)
        return (
            fitted.axis_start,
            fitted.axis_end,
            fitted.radius_start_m,
            fitted.radius_end_m,
            float(params["turns"]) if "turns" in params else fitted.turns,
            handedness,
            None,
        )
    turns = float(params["turns"]) if "turns" in params else 1.0
    return points[0], points[-1], radius_start, radius_end if radius_end is not None else radius_start, turns, handedness, None


def segment_length(segment: dict, points: list[Vec3]) -> float:
    kind = segment.get("kind")
    if kind in {"straight", "jump"}:
        return distance(points[0], points[1])
    if kind == "polyline":
        return polyline_length(points)
    if kind == "curve":
        return curve_length(points)
    if kind == "helix":
        start, end, radius_start, radius_end, turns, handedness, hint = _helix_args(segment, points)
        return helix_arc_length(start, end, radius_start, radius_end, turns, handedness, hint)
    raise CompileError(f"Unknown segment shape {kind!r}.")


def position_at(segment: dict, points: list[Vec3], fraction: float) -> Vec3:
    kind = segment.get("kind")
    if kind in {"straight", "jump"}:
        return position_on_polyline([points[0], points[1]], fraction)
    if kind == "polyline":
        return position_on_polyline(points, fraction)
    if kind == "curve":
        return curve_position(points, fraction)
    if kind == "helix":
        start, end, radius_start, radius_end, turns, handedness, hint = _helix_args(segment, points)
        return helix_position(start, end, radius_start, radius_end, turns, fraction, handedness, hint)
    raise CompileError(f"Unknown segment shape {kind!r}.")


def lit_length(run: dict, waypoints: dict[str, dict]) -> float:
    total = 0.0
    for segment in run.get("segments") or []:
        if not isinstance(segment, dict) or segment.get("kind") == "jump":
            continue
        total += segment_length(segment, points_for(segment, waypoints))
    return total


def pitch_mm_for_count(run: dict, waypoints: dict[str, dict], count: int) -> float:
    if count <= 0:
        raise CompileError("There are no pixels to scale the pitch to.")
    return lit_length(run, waypoints) / count * 1000.0


def compile_run(fixture_name: str, run: dict, waypoints: dict[str, dict]) -> list[Pixel]:
    pitch_m = float(run["pitchMm"]) / 1000.0
    if pitch_m <= 0:
        raise CompileError("Pitch has to be greater than zero.")
    pixels: list[Pixel] = []
    ordinal = 0
    cursor = 0.0
    index = int(run.get("startIndex") or 0)
    for segment in run.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        points = points_for(segment, waypoints)
        length = segment_length(segment, points)
        if segment.get("kind") == "jump":
            continue
        override = segment.get("pixelCountOverride")
        if isinstance(override, int) and not isinstance(override, bool):
            # Place exactly N pixels on this span, then keep the pitch clock on the
            # geometric length so the next span is spaced as if this count had not changed.
            for slot in range(override):
                fraction = 0.0 if override == 0 else slot / override
                point = position_at(segment, points, fraction)
                pixels.append(_pixel(fixture_name, run, segment, index, point, fraction))
                index += 1
            cursor += length
            ordinal = _pitch_ordinal(cursor, pitch_m)
            continue
        segment_end = cursor + length
        while ordinal * pitch_m < segment_end - EPS:
            if ordinal > 100_000:
                raise CompileError("That path would place more than 100000 pixels.")
            distance_along = ordinal * pitch_m
            fraction = 0.0 if length < EPS else (distance_along - cursor) / length
            fraction = min(max(fraction, 0.0), 1.0)
            point = position_at(segment, points, fraction)
            pixels.append(_pixel(fixture_name, run, segment, index, point, fraction))
            ordinal += 1
            index += 1
        cursor = segment_end
    return pixels


def _pitch_ordinal(cursor: float, pitch_m: float) -> int:
    """Smallest k whose pitch distance is at or past `cursor`.

    A pixel that lands exactly on a segment boundary belongs to the next segment,
    which is the same rule as the walk `k * pitch < segment_end`.
    """
    if pitch_m <= 0:
        return 0
    return math.ceil((cursor - EPS) / pitch_m)


def _pixel(fixture_name: str, run: dict, segment: dict, index: int, point: Vec3, fraction: float) -> Pixel:
    return Pixel(
        fixture=fixture_name,
        controller=str(run.get("controllerId") or ""),
        output=int(run.get("outputIndex") or 0),
        index=index,
        x=point.x,
        y=point.y,
        z=point.z,
        segment=str(segment.get("id") or ""),
        u=fraction,
    )


def compile_fixture(fixture: dict) -> list[Pixel]:
    waypoints = waypoint_index(fixture)
    pixels: list[Pixel] = []
    name = str(fixture.get("name") or fixture.get("id") or "")
    for run in fixture.get("runs") or []:
        if isinstance(run, dict):
            pixels.extend(compile_run(name, run, waypoints))
    return apply_pixel_corrections(fixture, pixels)


def apply_pixel_corrections(fixture: dict, pixels: list[Pixel]) -> list[Pixel]:
    """Measured camera positions override the shape. The path itself stays."""
    corrections = fixture.get("pixelCorrections")
    if not isinstance(corrections, list) or not corrections:
        return pixels
    runs = [run for run in fixture.get("runs") or [] if isinstance(run, dict)]
    located: dict[tuple[str, int], list] = {}
    for item in corrections:
        if not isinstance(item, dict):
            continue
        run_id = item.get("runId")
        index = item.get("index")
        position = item.get("position")
        if not isinstance(run_id, str) or isinstance(index, bool) or not isinstance(index, int):
            continue
        if not isinstance(position, list) or len(position) != 3:
            continue
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in position):
            continue
        located[(run_id, index)] = position
    if not located:
        return pixels
    updated: list[Pixel] = []
    for pixel in pixels:
        run_id = _run_id_for(runs, pixel)
        position = located.get((run_id, pixel.index)) if run_id else None
        if position is None:
            updated.append(pixel)
            continue
        updated.append(replace(pixel, x=float(position[0]), y=float(position[1]), z=float(position[2])))
    return updated


def _run_id_for(runs: list[dict], pixel: Pixel) -> str | None:
    for run in runs:
        if str(run.get("controllerId") or "") != pixel.controller:
            continue
        if int(run.get("outputIndex") or 0) != pixel.output:
            continue
        segments = run.get("segments") if isinstance(run.get("segments"), list) else []
        if any(isinstance(segment, dict) and segment.get("id") == pixel.segment for segment in segments):
            return str(run.get("id") or "")
    return None


def validate_counts(fixture: dict, pixels: list[Pixel] | None = None) -> list[dict]:
    compiled = pixels if pixels is not None else compile_fixture(fixture)
    reports = []
    for run in fixture.get("runs") or []:
        if not isinstance(run, dict):
            continue
        segment_ids = {segment.get("id") for segment in run.get("segments") or [] if isinstance(segment, dict)}
        placed = [
            pixel
            for pixel in compiled
            if pixel.segment in segment_ids and pixel.controller == run.get("controllerId")
        ]
        expected = int(run.get("pixelCount") or 0)
        difference = len(placed) - expected
        ratio = abs(difference) / expected if expected else 0.0
        reports.append(
            {
                "runId": run.get("id"),
                "controllerId": run.get("controllerId"),
                "output": int(run.get("outputIndex") or 0),
                "expected": expected,
                "placed": len(placed),
                "difference": difference,
                "ratio": ratio,
                "ok": expected == 0 or ratio <= 0.03,
            }
        )
    return reports
