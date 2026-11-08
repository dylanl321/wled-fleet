"""Three spatial effects. Colour is a function of position and time, like Pixelblaze render3D."""

from __future__ import annotations

import colorsys
import math

from app.mapper.compile import Pixel

EFFECTS = ("fill-bottom", "height-rainbow", "angle-spin")


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
