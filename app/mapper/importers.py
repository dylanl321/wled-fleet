"""Read a CSV, Pixelblaze map, or xLights custom model back into a fixture."""

from __future__ import annotations

import csv
import io
import json
import uuid
import xml.etree.ElementTree as ElementTree

from app.mapper.schema_check import require_valid


def fixture_from_pixels(name: str, rows: list[dict]) -> dict:
    """One pixel becomes one waypoint. Each span is a single forced pixel at that point."""
    grouped: dict[tuple[str, int], list[dict]] = {}
    order: list[tuple[str, int]] = []
    for row in rows:
        key = (str(row.get("controller") or "imported"), int(row.get("output") or 0))
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append(row)
    waypoints = []
    runs = []
    for controller, output in order:
        group = sorted(grouped[(controller, output)], key=lambda item: int(item.get("index") or 0))
        segments = []
        for row in group:
            waypoint_id = str(uuid.uuid4())
            waypoints.append(
                {
                    "id": waypoint_id,
                    "position": [float(row["x"]), float(row["y"]), float(row["z"])],
                    "source": "imported",
                    "confidence": 1,
                    "label": f"pixel {int(row.get('index') or 0)}",
                }
            )
            segments.append(
                {
                    "id": str(uuid.uuid4()),
                    "kind": "straight",
                    "waypointIds": [waypoint_id, waypoint_id],
                    "params": {},
                    "pixelCountOverride": 1,
                }
            )
        runs.append(
            {
                "id": str(uuid.uuid4()),
                "controllerId": controller,
                "outputIndex": output,
                "startIndex": int(group[0].get("index") or 0) if group else 0,
                "pixelCount": len(group),
                "pitchMm": 16.666666666666668,
                "colorOrder": "GRB",
                "segments": segments,
            }
        )
    document = {
        "schemaVersion": 1,
        "id": str(uuid.uuid4()),
        "name": name or "Imported",
        "origin": {"position": [0, 0, 0], "orientation": [0, 0, 0, 1]},
        "surfaceHint": "freeform",
        "waypoints": waypoints,
        "runs": runs,
    }
    require_valid(document)
    return document


def import_csv(text: str, name: str) -> dict:
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for raw in reader:
        rows.append(
            {
                "controller": raw.get("controller") or "imported",
                "output": int(float(raw.get("output") or 0)),
                "index": int(float(raw.get("index") or 0)),
                "x": float(raw["x_m"] if "x_m" in raw else raw["x"]),
                "y": float(raw["y_m"] if "y_m" in raw else raw["y"]),
                "z": float(raw["z_m"] if "z_m" in raw else raw["z"]),
            }
        )
    if not rows:
        raise ValueError("The CSV has no pixels.")
    return fixture_from_pixels(name, rows)


def import_pixelblaze(text: str, name: str) -> dict:
    parsed = json.loads(text)
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("A Pixelblaze map is a JSON array of [x, y, z].")
    rows = []
    for index, item in enumerate(parsed):
        if not isinstance(item, list) or len(item) < 3:
            raise ValueError(f"Pixel {index} is not [x, y, z].")
        rows.append({"controller": "imported", "output": 0, "index": index, "x": item[0], "y": item[1], "z": item[2]})
    return fixture_from_pixels(name, rows)


def import_xmodel(text: str, name: str, spacing_m: float = 0.05) -> dict:
    root = ElementTree.fromstring(text)
    node = root if _local(root.tag) in {"custommodel", "model"} else None
    if node is None:
        for child in root.iter():
            if _local(child.tag) in {"custommodel", "model"} and (
                child.attrib.get("CustomModel") or child.attrib.get("CustomModelCompressed")
            ):
                node = child
                break
    if node is None:
        raise ValueError("No custom model was found in that file.")
    grid = node.attrib.get("CustomModel") or ""
    width = int(float(node.attrib.get("CustomWidth") or node.attrib.get("parm1") or 1))
    height = int(float(node.attrib.get("CustomHeight") or node.attrib.get("parm2") or 1))
    rows = _cells(grid, width, height, spacing_m)
    if not rows and node.attrib.get("CustomModelCompressed"):
        rows = _compressed(node.attrib["CustomModelCompressed"], spacing_m)
    if not rows:
        raise ValueError("That model has no pixels.")
    return fixture_from_pixels(name or node.attrib.get("name") or "Imported", rows)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _cells(grid: str, width: int, height: int, spacing: float) -> list[dict]:
    layers = [layer for layer in grid.split("|")]
    rows = []
    for layer_index, layer in enumerate(layers):
        for row_index, row in enumerate(layer.split(";")):
            for column, cell in enumerate(row.split(",")):
                text = cell.strip()
                if not text:
                    continue
                number = int(float(text))
                if number <= 0:
                    continue
                rows.append(
                    {
                        "controller": "imported",
                        "output": 0,
                        "index": number - 1,
                        "x": column * spacing,
                        "y": (max(height, 1) - 1 - row_index) * spacing,
                        "z": layer_index * spacing,
                    }
                )
    rows.sort(key=lambda item: item["index"])
    return rows


def _compressed(text: str, spacing: float) -> list[dict]:
    """node,row,col,layer groups separated by semicolons."""
    rows = []
    for chunk in text.split(";"):
        parts = [part.strip() for part in chunk.split(",") if part.strip()]
        if len(parts) < 4:
            continue
        number, row, column, layer = (int(float(part)) for part in parts[:4])
        if number <= 0:
            continue
        rows.append(
            {
                "controller": "imported",
                "output": 0,
                "index": number - 1,
                "x": column * spacing,
                "y": row * spacing,
                "z": layer * spacing,
            }
        )
    rows.sort(key=lambda item: item["index"])
    return rows
