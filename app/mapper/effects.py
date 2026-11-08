"""Three spatial effects. Colour is a function of position and time, like Pixelblaze render3D."""

from __future__ import annotations

import colorsys
import math

from app.mapper.compile import Pixel

EFFECTS = ("fill-bottom", "height-rainbow", "angle-spin")


def targeted_colors(
    pixels: list[Pixel],
    name: str,
    phase: float,
    params: dict | None = None,
    fixture: dict | None = None,
) -> list[tuple[int, int, int]]:
    """Colour the whole fixture, one named branch, or a height band."""
    params = params or {}
    chosen = [index for index, pixel in enumerate(pixels) if _selected(pixel, params, fixture)]
    output = [(0, 0, 0)] * len(pixels)
    if not chosen:
        return output
    colors = effect_colors([pixels[index] for index in chosen], name, phase)
    for index, color in zip(chosen, colors):
        output[index] = color
    return output


def _selected(pixel: Pixel, params: dict, fixture: dict | None) -> bool:
    segment = params.get("segment")
    if isinstance(segment, str) and segment and pixel.segment != segment:
        return False
    branch = params.get("branch")
    if isinstance(branch, str) and branch:
        if not _branch_match(pixel.segment, branch, fixture):
            return False
    if "height" in params and params.get("height") is not None:
        try:
            height = float(params["height"])
            band = float(params.get("band") or 0.15)
        except (TypeError, ValueError):
            return True
        if abs(pixel.y - height) > band:
            return False
    return True


def _branch_match(segment_id: str, branch: str, fixture: dict | None) -> bool:
    needle = branch.strip().lower()
    if not needle:
        return True
    if needle == segment_id.lower():
        return True
    if not isinstance(fixture, dict):
        return needle in segment_id.lower()
    labels = {}
    for waypoint in fixture.get("waypoints") or []:
        if isinstance(waypoint, dict):
            labels[str(waypoint.get("id"))] = str(waypoint.get("label") or "")
    for run in fixture.get("runs") or []:
        if not isinstance(run, dict):
            continue
        for segment in run.get("segments") or []:
            if not isinstance(segment, dict) or segment.get("id") != segment_id:
                continue
            names = [labels.get(str(waypoint_id), "") for waypoint_id in segment.get("waypointIds") or []]
            blob = " ".join([str(segment.get("id") or ""), *names]).lower()
            return needle in blob
    return needle in segment_id.lower()


def effect_colors(pixels: list[Pixel], name: str, phase: float) -> list[tuple[int, int, int]]:
    if name not in EFFECTS:
        raise ValueError(f"Unknown effect {name}.")
    if not pixels:
        return []
    cycle = phase % 1.0
    if phase > 0 and math.isclose(cycle, 0.0, abs_tol=1e-12):
        cycle = 1.0
    phase = cycle
    low = min(pixel.y for pixel in pixels)
    high = max(pixel.y for pixel in pixels)
    height = max(high - low, 1e-6)
    colors: list[tuple[int, int, int]] = []
    for pixel in pixels:
        if name == "fill-bottom":
            threshold = low + phase * height
            colors.append((255, 244, 230) if pixel.y <= threshold + 1e-9 else (0, 0, 0))
        elif name == "height-rainbow":
            hue = ((pixel.y - low) / height + phase) % 1.0
            colors.append(_hsv(hue))
        else:
            hue = (math.atan2(pixel.x, pixel.z) / (2.0 * math.pi) + 0.5 + phase) % 1.0
            colors.append(_hsv(hue))
    return colors


def _hsv(hue: float) -> tuple[int, int, int]:
    red, green, blue = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
    return (round(red * 255), round(green * 255), round(blue * 255))
