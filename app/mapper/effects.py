"""Spatial effects. Colour is a function of position and time, like Pixelblaze render3D.

Every effect accepts a target (a group name, the trunk, every branch, or all)
and branch-relative coordinates. heightM is fixture height. branchHeightM is
height above that group's lowest pixel. branchU is 0 at the trunk side of a
group and 1 at the far end.
"""

from __future__ import annotations

import colorsys
import math

from app.mapper.compile import Pixel

EFFECTS = ("fill-bottom", "height-rainbow", "angle-spin", "branch-chase")
_COMET_WIDTH = 0.18


def effect_colors(
    pixels: list[Pixel],
    name: str,
    phase: float,
    params: dict | None = None,
) -> list[tuple[int, int, int]]:
    if name not in EFFECTS:
        raise ValueError(f"Unknown effect {name}.")
    options = params or {}
    _require_target(pixels, options)
    _require_numbers(options)
    if not pixels:
        return []
    cycle = phase % 1.0
    if phase > 0 and math.isclose(cycle, 0.0, abs_tol=1e-12):
        cycle = 1.0
    addressed = [_addressed(pixel, options) for pixel in pixels]
    chosen = [pixel for pixel, keep in zip(pixels, addressed) if keep]
    low = min((pixel.y for pixel in chosen), default=0.0)
    high = max((pixel.y for pixel in chosen), default=0.0)
    height = max(high - low, 1e-6)
    colors: list[tuple[int, int, int]] = []
    for pixel, keep in zip(pixels, addressed):
        if not keep:
            colors.append((0, 0, 0))
            continue
        if name == "fill-bottom":
            threshold = low + cycle * height
            colors.append((255, 244, 230) if pixel.y <= threshold + 1e-9 else (0, 0, 0))
        elif name == "height-rainbow":
            hue = ((pixel.y - low) / height + cycle) % 1.0
            colors.append(_hsv(hue))
        elif name == "angle-spin":
            hue = (math.atan2(pixel.x, pixel.z) / (2.0 * math.pi) + 0.5 + cycle) % 1.0
            colors.append(_hsv(hue))
        elif name == "branch-chase":
            colors.append(_comet(pixel.branch_u, cycle))
        else:
            raise ValueError(f"Unknown effect {name}.")
    return colors


def _comet(branch_u: float, phase: float) -> tuple[int, int, int]:
    """A short pulse that leaves the root (u = 0) and runs to the tip."""
    delta = phase - branch_u
    if delta < -1e-9 or delta > _COMET_WIDTH:
        return (0, 0, 0)
    gain = 1.0 - max(delta, 0.0) / _COMET_WIDTH
    return (round(255 * gain), round(244 * gain), round(230 * gain))


def _require_target(pixels: list[Pixel], params: dict) -> None:
    target = _target_key(params.get("target"))
    if target == "all":
        return
    if any(_matches_target(pixel, target) for pixel in pixels):
        return
    raise ValueError("Nothing on this fixture matches that.")


def _require_numbers(params: dict) -> None:
    for key in ("heightM", "branchHeightM", "branchU", "bandM", "bandU", "seconds"):
        if key not in params or params[key] is None:
            continue
        _number(params[key], key)
    for key in ("bandM", "bandU"):
        if key in params and params[key] is not None and _number(params[key], key) < 0:
            raise ValueError(f"{key} has to be zero or more.")


def _addressed(pixel: Pixel, params: dict) -> bool:
    if not _matches_target(pixel, _target_key(params.get("target"))):
        return False
    if not _near(pixel.y, params.get("heightM"), params.get("bandM"), 0.05):
        return False
    if not _near(pixel.branch_height, params.get("branchHeightM"), params.get("bandM"), 0.05):
        return False
    if not _near(pixel.branch_u, params.get("branchU"), params.get("bandU"), 0.05):
        return False
    return True


def _near(value: float, center: object, band: object, default_band: float) -> bool:
    if center is None:
        return True
    width = default_band if band is None else _number(band, "band")
    return abs(value - _number(center, "coordinate")) <= width + 1e-9


def _target_key(value: object) -> str:
    if value is None:
        return "all"
    if not isinstance(value, str):
        raise ValueError("The target has to be a group name.")
    key = value.strip().lower()
    if key in {"", "all"}:
        return "all"
    if key in {"branches", "all branches"}:
        return "branches"
    if key in {"trunk", "the trunk"}:
        return "trunk"
    return key


def _matches_target(pixel: Pixel, target: str) -> bool:
    if target == "all":
        return True
    if target == "branches":
        return pixel.group_role == "branch"
    if target == "trunk":
        return pixel.group_role == "trunk"
    return target == pixel.group.lower() or (pixel.group_name != "" and target == pixel.group_name.lower())


def _number(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} has to be a number.")
    return float(value)


def _hsv(hue: float) -> tuple[int, int, int]:
    red, green, blue = colorsys.hsv_to_rgb(hue % 1.0, 1.0, 1.0)
    return (round(red * 255), round(green * 255), round(blue * 255))
