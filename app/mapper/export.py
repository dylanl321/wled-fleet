"""CSV, JSON, Pixelblaze, and xLights Custom Model exports."""

from __future__ import annotations

import csv
import io
import json
from xml.sax.saxutils import quoteattr

from app.mapper.compile import Pixel
from app.mapper.ledmap import grid_map


def pixels_csv(pixels: list[Pixel]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["fixture", "controller", "output", "index", "x_m", "y_m", "z_m", "segment", "u"])
    for pixel in pixels:
        writer.writerow(
            [
                pixel.fixture,
                pixel.controller,
                pixel.output,
                pixel.index,
                f"{pixel.x:.6f}",
                f"{pixel.y:.6f}",
                f"{pixel.z:.6f}",
                pixel.segment,
                f"{pixel.u:.6f}",
            ]
        )
    return buffer.getvalue()


def pixels_json(fixture_id: str, pixels: list[Pixel]) -> str:
    return json.dumps(
        {"fixtureId": fixture_id, "pixels": [pixel.as_dict() for pixel in pixels]},
        indent=2,
    )


def pixelblaze_map(pixels: list[Pixel]) -> str:
    """JSON array of [x, y, z] in controller, output, then index order."""
    ordered = sorted(pixels, key=lambda pixel: (pixel.controller, pixel.output, pixel.index))
    return json.dumps([[pixel.x, pixel.y, pixel.z] for pixel in ordered])


def xlights_xmodel(
    name: str,
    pixels: list[Pixel],
    *,
    surface_hint: str,
    width: int,
    height: int,
    depth: int = 1,
    controller_name: str = "",
) -> str:
    """2D Custom Model. depth > 1 adds pipe-separated layers for a cheap 3D model.

    Node numbers are 1-based. A second controller continues after the first so
    two outputs that both start at index 0 do not share a node number.
    """
    numbers = _node_numbers(pixels)
    numbered = [
        Pixel(
            pixel.fixture,
            pixel.controller,
            pixel.output,
            numbers[(pixel.controller, pixel.output, pixel.index)],
            pixel.x,
            pixel.y,
            pixel.z,
            pixel.segment,
            pixel.u,
        )
        for pixel in pixels
    ]
    grid = grid_map(numbered, width=width, height=height, surface_hint=surface_hint, depth=depth)
    layers = _layers(grid["map"], width, height, depth)
    custom = "|".join(";".join(",".join(cell) for cell in rows) for rows in layers)
    attributes = {
        "name": name,
        "parm1": str(width),
        "parm2": str(height),
        "CustomWidth": str(width),
        "CustomHeight": str(height),
        "Depth": str(depth),
        "StringType": "RGB Nodes",
        "PixelCount": str(len(pixels)),
        "StartChannel": "1",
        "DisplayAs": "Custom",
        "Transparency": "0",
        "PixelSize": "2",
        "Antialias": "1",
        "SourceVersion": "2025.12",
        "CustomModel": custom,
        "Dir": "L",
        "StartSide": "B",
    }
    if controller_name:
        attributes["Controller"] = controller_name
    body = " ".join(f"{key}={quoteattr(value)}" for key, value in attributes.items())
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<custommodel {body}></custommodel>\n'


def _node_numbers(pixels: list[Pixel]) -> dict[tuple[str, int, int], int]:
    controllers: list[str] = []
    for pixel in pixels:
        if pixel.controller not in controllers:
            controllers.append(pixel.controller)
    bases: dict[str, int] = {}
    cursor = 0
    for controller in controllers:
        bases[controller] = cursor
        owned = [pixel.index for pixel in pixels if pixel.controller == controller]
        cursor += (max(owned) + 1) if owned else 0
    return {
        (pixel.controller, pixel.output, pixel.index): bases[pixel.controller] + pixel.index + 1
        for pixel in pixels
    }


def _layers(flat: list[int], width: int, height: int, depth: int) -> list[list[list[str]]]:
    layers: list[list[list[str]]] = []
    cursor = 0
    for _ in range(depth):
        rows: list[list[str]] = []
        for _row in range(height):
            row: list[str] = []
            for _column in range(width):
                value = flat[cursor]
                cursor += 1
                row.append("" if value < 0 else str(value))
            rows.append(row)
        layers.append(rows)
    return layers
