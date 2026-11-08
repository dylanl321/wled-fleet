"""WLED ledmap.json builders.

map[logical] is the physical pixel index. -1 is an empty cell. That matches
WLED's 2D examples: a serpentine cell holds the wire index that belongs there.
"""

from __future__ import annotations

import math

from app.mapper.compile import Pixel


def linear_by_height(pixels: list[Pixel]) -> dict:
    ordered = sorted(pixels, key=lambda pixel: (pixel.y, pixel.x, pixel.z, pixel.index))
    return {"map": [pixel.index for pixel in ordered]}


def grid_map(
    pixels: list[Pixel],
    *,
    width: int,
    height: int,
    surface_hint: str,
    depth: int = 1,
) -> dict:
    if width < 1 or height < 1 or depth < 1:
        raise ValueError("Grid width, height, and depth have to be at least 1.")
    if not pixels:
        blank = [-1] * (width * height)
        return {"width": width, "height": height, "depth": depth, "map": blank * depth}
    cells = _assign(pixels, width, height, depth, surface_hint)
    flat: list[int] = []
    for layer in range(depth):
        for row in range(height):
            for column in range(width):
                flat.append(cells[layer][row][column])
    document = {"width": width, "height": height, "map": flat}
    if depth > 1:
        document["depth"] = depth
    return document


def _assign(
    pixels: list[Pixel],
    width: int,
    height: int,
    depth: int,
    surface_hint: str,
) -> list[list[list[int]]]:
    cells = [[[-1 for _ in range(width)] for _ in range(height)] for _ in range(depth)]
    coords = [_coordinates(pixel, surface_hint) for pixel in pixels]
    lows, highs = _bounds(coords, depth)
    pending: list[tuple[int, int, int, Pixel]] = []
    for pixel, (u_coord, v_coord, w_coord) in zip(pixels, coords):
        column = _bin(u_coord, lows[0], highs[0], width)
        row = _bin(v_coord, lows[1], highs[1], height)
        layer = 0 if depth == 1 else _bin(w_coord, lows[2], highs[2], depth)
        pending.append((layer, row, column, pixel))
    pending.sort(key=lambda item: item[3].index)
    for layer, row, column, pixel in pending:
        if cells[layer][row][column] == -1:
            cells[layer][row][column] = pixel.index
            continue
        placed = _nearest_empty(cells, layer, row, column)
        if placed is None:
            raise ValueError("That grid is too small for these pixels. Use a larger width or height.")
        layer_n, row_n, column_n = placed
        cells[layer_n][row_n][column_n] = pixel.index
    return cells


def _coordinates(pixel: Pixel, surface_hint: str) -> tuple[float, float, float]:
    if surface_hint in {"cylindrical", "branching"}:
        return math.atan2(pixel.x, pixel.z), pixel.y, math.hypot(pixel.x, pixel.z)
    return pixel.x, pixel.y, pixel.z


def _bounds(coords: list[tuple[float, float, float]], depth: int) -> tuple[list[float], list[float]]:
    lows = [min(item[axis] for item in coords) for axis in range(3)]
    highs = [max(item[axis] for item in coords) for axis in range(3)]
    if depth == 1:
        lows[2], highs[2] = 0.0, 1.0
    return lows, highs


def _bin(value: float, low: float, high: float, count: int) -> int:
    if count <= 1 or high <= low:
        return 0
    fraction = (value - low) / (high - low)
    fraction = min(max(fraction, 0.0), 0.999999)
    return min(count - 1, int(fraction * count))


def _nearest_empty(
    cells: list[list[list[int]]],
    layer: int,
    row: int,
    column: int,
) -> tuple[int, int, int] | None:
    depth = len(cells)
    height = len(cells[0])
    width = len(cells[0][0])
    for radius in range(1, max(width, height, depth) + 1):
        for layer_n in range(max(0, layer - radius), min(depth, layer + radius + 1)):
            for row_n in range(max(0, row - radius), min(height, row + radius + 1)):
                for column_n in range(max(0, column - radius), min(width, column + radius + 1)):
                    if cells[layer_n][row_n][column_n] == -1:
                        return layer_n, row_n, column_n
    return None
