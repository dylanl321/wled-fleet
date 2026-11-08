"""CSV, JSON, Pixelblaze, and xLights model exports.

xLights 2026.04 replaced raw XML nodes with a serialising visitor and renamed
parm1/parm2/parm3 to descriptive attributes. Current xLights (release 2026.19,
serializer read from master at 2026.19.1) writes an exported model as::

    <models type="exported">
      <model name="..." DisplayAs="Custom|Poly Line" SourceVersion="2026.19" .../>
    </models>

Custom models store the grid in CustomModel when the grid is dense, and in
CustomModelCompressed (node,row,col[,layer]) when fewer than 80% of the cells
are filled. Depth greater than 1 is a true 3D model: extra grid layers, or a
layer index on each compressed node. Poly lines store corner points in
PointData. parm1/parm2 are still accepted on import and are not written.
"""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET

from app.mapper.compile import Pixel, compile_fixture, points_for, waypoint_index
from app.mapper.controllers import channels_for_order
from app.mapper.geom import Vec3
from app.mapper.ledmap import grid_map
from app.mapper.wiring import wiring_attrs, wiring_block

# xlights_version_string of the 2026.19 release. Master after that tag defaults
# the same serializer to 2026.19.1; the attribute is the version string the
# writer was built with, and current xLights does not reject other values.
XLIGHTS_SOURCE_VERSION = "2026.19"
# CUR_MODEL_POS_VER in src-core/models/ModelScreenLocation.h
XLIGHTS_MODEL_VERSION = "8"


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
    controllers: list[dict] | None = None,
) -> str:
    """Custom model in the current xLights export wrapper.

    Node numbers are 1-based. A second controller continues after the first so
    two outputs that both start at index 0 do not share a node number. depth
    greater than 1 writes a 3D custom model (Depth plus one grid layer, or a
    layer index in the compressed form).
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
    cells = _cells(grid["map"], width, height, depth)
    payload_name, payload = _custom_payload(cells)
    wiring = _wiring_from_pixels(pixels, controllers)
    label = wiring[0]["controller"] if wiring else controller_name
    start = str(wiring[0]["startChannel"]) if wiring else "1"
    attributes = _common_attributes(name, "Custom", label, start)
    attributes.update(
        {
            "CustomWidth": str(width),
            "CustomHeight": str(height),
            "Depth": str(depth),
            "PixelCount": str(len(pixels)),
            "ScaleX": "1.000000",
            "ScaleY": "1.000000",
            "ScaleZ": "1.000000",
            "RotateX": "0.000000",
            "RotateY": "0.000000",
            "RotateZ": "0.000000",
            payload_name: payload,
        }
    )
    return _document([{"attributes": attributes, "wiring": wiring}])


def xlights_polyline(name: str, fixture: dict, controllers: list[dict] | None = None) -> str:
    """Poly Line model for a fixture whose runs are straight pieces.

    One <model> per run. PointData is the corner list in wire order, in metres,
    formatted the way xLights writes PointData. Jumps, helices, and curves are
    refused: a poly line would draw pixels across the bare wire or invent a
    straight chord.
    """
    runs = [run for run in fixture.get("runs") or [] if isinstance(run, dict)]
    if not runs:
        raise ValueError("This fixture has no runs to export as a line.")
    waypoints = waypoint_index(fixture)
    pixels = compile_fixture(fixture)
    models: list[dict] = []
    channel = 1
    for run in runs:
        _require_line_run(run)
        points = _run_points(run, waypoints)
        count = _run_pixel_count(run, pixels)
        if count < 1:
            raise ValueError("This line has no pixels.")
        block = wiring_block(
            controller_id=str(run.get("controllerId") or ""),
            output=int(run.get("outputIndex") or 0),
            start_index=int(run.get("startIndex") or 0),
            pixel_count=int(run.get("pixelCount") or count),
            color_order=str(run.get("colorOrder") or "GRB"),
            controllers=controllers,
        )
        start = int(block["startChannel"]) if block["known"] else channel
        block = dict(block)
        block["startChannel"] = start
        label = name if len(runs) == 1 else f"{name} {int(run.get('outputIndex') or 0) + 1}"
        attributes = _common_attributes(label, "Poly Line", str(block["controller"]), str(start))
        attributes.update(
            {
                "LightsPerNode": "1",
                "NodesPerString": str(count),
                "ScaleX": "1.000000",
                "ScaleY": "1.000000",
                "ScaleZ": "1.000000",
                "NumPoints": str(len(points)),
                "PointData": _point_data(points),
                "cPointData": "",
                "PolyStrings": "1",
                "AlternateNodes": "false",
                "DropPattern": "1",
                "SegsExpanded": "TRUE",
                "ModelHeight": "1.000000",
                "WledOutput": str(int(block["output"])),
                "WledStart": str(int(run.get("startIndex") or 0)),
                "WledPitch": f"{float(run.get('pitchMm') or 0):.6f}",
            }
        )
        for index in range(len(points)):
            attributes[f"Corner{index + 1}"] = "Neither"
        models.append({"attributes": attributes, "wiring": [block]})
        if not block["known"]:
            channel += count * channels_for_order(str(run.get("colorOrder") or "GRB"))
    return _document(models)


def _require_line_run(run: dict) -> None:
    segments = [segment for segment in run.get("segments") or [] if isinstance(segment, dict)]
    if not segments:
        raise ValueError("This fixture has no path to export as a line.")
    for segment in segments:
        kind = segment.get("kind")
        if kind not in {"straight", "polyline"}:
            raise ValueError("A line export needs straight or polyline runs. Use the xLights grid for a helix, curve, or jump.")
        override = segment.get("pixelCountOverride")
        if isinstance(override, int) and not isinstance(override, bool):
            raise ValueError("A line export follows the pitch along the path. Clear the pixel override on a segment, or use the xLights grid.")


def _run_points(run: dict, waypoints: dict[str, dict]) -> list[Vec3]:
    points: list[Vec3] = []
    for segment in run.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        span = points_for(segment, waypoints)
        if segment.get("kind") == "straight":
            span = span[:2]
        if points and _same_point(points[-1], span[0]):
            span = span[1:]
        points.extend(span)
    if len(points) < 2:
        raise ValueError("A line needs at least two points.")
    return points


def _run_pixel_count(run: dict, pixels: list[Pixel]) -> int:
    segment_ids = {segment.get("id") for segment in run.get("segments") or [] if isinstance(segment, dict)}
    return sum(
        1
        for pixel in pixels
        if pixel.segment in segment_ids
        and pixel.controller == str(run.get("controllerId") or "")
        and pixel.output == int(run.get("outputIndex") or 0)
    )


def _same_point(left: Vec3, right: Vec3) -> bool:
    return abs(left.x - right.x) < 1e-6 and abs(left.y - right.y) < 1e-6 and abs(left.z - right.z) < 1e-6


def _point_data(points: list[Vec3]) -> str:
    parts: list[str] = []
    for point in points:
        parts.append(f"{point.x:.6f}")
        parts.append(f"{point.y:.6f}")
        parts.append(f"{point.z:.6f}")
    return ",".join(parts)


def _common_attributes(name: str, display_as: str, controller_name: str, start_channel: str = "1") -> dict[str, str]:
    return {
        "name": name,
        "DisplayAs": display_as,
        "LayoutGroup": "Default",
        "StartSide": "B",
        "Dir": "L",
        "Antialias": "1",
        "PixelSize": "2",
        "StringType": "RGB Nodes",
        "Transparency": "0",
        "StartChannel": start_channel,
        "Controller": controller_name,
        "versionNumber": XLIGHTS_MODEL_VERSION,
        "SourceVersion": XLIGHTS_SOURCE_VERSION,
        "WorldPosX": "0.000000",
        "WorldPosY": "0.000000",
        "WorldPosZ": "0.000000",
    }


def _wiring_from_pixels(pixels: list[Pixel], controllers: list[dict] | None) -> list[dict]:
    keys: list[tuple[str, int]] = []
    for pixel in pixels:
        key = (pixel.controller, pixel.output)
        if key not in keys:
            keys.append(key)
    blocks = []
    for controller_id, output in keys:
        owned = [pixel for pixel in pixels if pixel.controller == controller_id and pixel.output == output]
        start_pixel = min(pixel.index for pixel in owned)
        order = "GRB"
        block = wiring_block(
            controller_id=controller_id,
            output=output,
            start_index=0,
            pixel_count=len(owned),
            color_order=order,
            controllers=controllers,
        )
        channels = int(block["channels"])
        if block["known"]:
            origin_pixel = (int(block["startChannel"]) - 1) // channels
            if start_pixel > origin_pixel:
                block["startChannel"] = start_pixel * channels + 1
        else:
            block["startChannel"] = start_pixel * channels + 1
            block["pixelCount"] = len(owned)
        blocks.append(block)
    return blocks


def _document(models: list[dict]) -> str:
    root = ET.Element("models", {"type": "exported"})
    for item in models:
        attributes = item["attributes"] if "attributes" in item else item
        wiring = item.get("wiring") or [] if isinstance(item, dict) else []
        node = ET.SubElement(root, "model", _sorted_attributes(attributes))
        for block in wiring:
            ET.SubElement(node, "wiring", wiring_attrs(block))
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


def _sorted_attributes(attributes: dict[str, str]) -> dict[str, str]:
    def rank(key: str) -> tuple[int, str]:
        if key == "name":
            return (0, "")
        if key == "DisplayAs":
            return (1, "")
        if "CustomModel" in key:
            return (3, key.lower())
        return (2, key.lower())

    return {key: attributes[key] for key in sorted(attributes, key=rank)}


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


def _cells(flat: list[int], width: int, height: int, depth: int) -> list[list[list[int]]]:
    layers: list[list[list[int]]] = []
    cursor = 0
    for _ in range(depth):
        rows: list[list[int]] = []
        for _row in range(height):
            row: list[int] = []
            for _column in range(width):
                row.append(flat[cursor])
                cursor += 1
            rows.append(row)
        layers.append(rows)
    return layers


def _custom_payload(cells: list[list[list[int]]]) -> tuple[str, str]:
    """Match CustomModel::ToCompressed: compress only when the grid is under 80% full."""
    nodes = 0
    total = 0
    for layer in cells:
        for row in layer:
            for value in row:
                total += 1
                if value >= 0:
                    nodes += 1
    if total and nodes > 0.80 * total:
        return "CustomModel", _custom_model_string(cells)
    compressed = _compressed_string(cells)
    if compressed:
        return "CustomModelCompressed", compressed
    return "CustomModel", _custom_model_string(cells)


def _custom_model_string(cells: list[list[list[int]]]) -> str:
    layers: list[str] = []
    for layer in cells:
        rows: list[str] = []
        for row in layer:
            rows.append(",".join("" if value < 0 else str(value) for value in row))
        layers.append(";".join(rows))
    return "|".join(layers)


def _compressed_string(cells: list[list[list[int]]]) -> str:
    parts: list[str] = []
    for layer_index, layer in enumerate(cells):
        for row_index, row in enumerate(layer):
            for column_index, value in enumerate(row):
                if value < 0:
                    continue
                item = f"{value},{row_index},{column_index}"
                if len(cells) > 1:
                    item = f"{item},{layer_index}"
                parts.append(item)
    return ";".join(parts)
