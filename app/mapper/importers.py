"""Build a fixture from a CSV pixel table, a Pixelblaze map, or an xLights model.

Imported pixels are stored as waypoints. A coordinate table (CSV, JSON, or
Pixelblaze) keeps each pixel on its own point, so compiling the fixture
reproduces the file. An xLights custom model places each node on its grid
cell. A poly line keeps the corner points and the pixel count, and the
compiler walks that path at the file's pitch.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
import xml.etree.ElementTree as ET

from app.mapper.geom import Vec3, polyline_length

CELL_MM = 1000.0 / 60.0
MAX_TEXT = 2_000_000


def import_text(
    text: str,
    *,
    filename: str = "",
    name: str = "",
    controller_id: str = "",
    output_index: int = 0,
) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("That file is empty.")
    if len(text) > MAX_TEXT:
        raise ValueError("That file is too large to import.")
    kind = _detect(text, filename)
    if kind == "xmodel":
        return _from_xmodel(text, name=name, controller_id=controller_id, output_index=output_index)
    if kind == "pixelblaze":
        payload = _load_json(text)
        if isinstance(payload, list):
            return _from_triples(
                payload,
                name=name or _stem(filename) or "Imported",
                controller_id=controller_id or "imported",
                output_index=output_index,
            )
        if isinstance(payload, dict):
            return _from_pixel_document(
                payload,
                name=name or _stem(filename) or "Imported",
                controller_id=controller_id,
                output_index=output_index,
            )
        raise ValueError("A Pixelblaze map is a JSON array of [x, y, z].")
    if kind == "csv":
        return _from_csv(
            text,
            name=name or _stem(filename) or "Imported",
            controller_id=controller_id,
            output_index=output_index,
        )
    raise ValueError("Choose a CSV, a Pixelblaze map, or an xLights model.")


def _detect(text: str, filename: str) -> str:
    stripped = text.lstrip("\ufeff \t\r\n")
    lower = filename.lower()
    if lower.endswith(".xmodel") or stripped.startswith("<"):
        return "xmodel"
    if lower.endswith(".csv"):
        return "csv"
    if stripped.startswith("[") or stripped.startswith("{"):
        return "pixelblaze"
    first = stripped.splitlines()[0].lower() if stripped else ""
    if "x_m" in first or first.startswith("fixture,") or "y_m" in first:
        return "csv"
    raise ValueError("Choose a CSV, a Pixelblaze map, or an xLights model.")


def _load_json(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("That JSON could not be read.") from exc


def _stem(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base.strip()[:80]


def _from_csv(text: str, *, name: str, controller_id: str, output_index: int) -> dict:
    sample = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(sample))
    if reader.fieldnames is None:
        raise ValueError("That CSV has no header.")
    fields = {_normal_header(field): field for field in reader.fieldnames if field}
    rows: list[dict] = []
    for raw in reader:
        if not isinstance(raw, dict) or not any(raw.values()):
            continue
        rows.append(_row_from_csv(raw, fields))
    if not rows:
        raise ValueError("That CSV has no pixels.")
    return _from_rows(rows, name=name, controller_id=controller_id, output_index=output_index)


def _normal_header(field: str) -> str:
    return field.strip().lower().replace(" ", "")


def _row_from_csv(raw: dict, fields: dict[str, str]) -> dict:
    def cell(*names: str) -> str:
        for name in names:
            key = fields.get(name)
            if key is None:
                continue
            value = raw.get(key)
            if value is not None and str(value).strip() != "":
                return str(value).strip()
        return ""

    try:
        point = (
            float(cell("x_m", "x")),
            float(cell("y_m", "y")),
            float(cell("z_m", "z")),
        )
    except ValueError as exc:
        raise ValueError("Each CSV row needs x_m, y_m, and z_m.") from exc
    index_text = cell("index")
    output_text = cell("output")
    try:
        index = int(index_text) if index_text else None
        output = int(output_text) if output_text else None
    except ValueError as exc:
        raise ValueError("CSV index and output need to be whole numbers.") from exc
    return {
        "fixture": cell("fixture"),
        "controller": cell("controller"),
        "output": output,
        "index": index,
        "point": point,
    }


def _from_pixel_document(payload: dict, *, name: str, controller_id: str, output_index: int) -> dict:
    pixels = payload.get("pixels")
    if not isinstance(pixels, list) or not pixels:
        raise ValueError("That JSON has no pixels.")
    rows: list[dict] = []
    for item in pixels:
        if not isinstance(item, dict):
            raise ValueError("Each pixel needs x, y, and z.")
        try:
            point = (
                float(item.get("x", item.get("x_m"))),
                float(item.get("y", item.get("y_m"))),
                float(item.get("z", item.get("z_m"))),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Each pixel needs x, y, and z.") from exc
        output = item.get("output")
        index = item.get("index")
        rows.append(
            {
                "fixture": str(item.get("fixture") or ""),
                "controller": str(item.get("controller") or ""),
                "output": int(output) if isinstance(output, int) and not isinstance(output, bool) else None,
                "index": int(index) if isinstance(index, int) and not isinstance(index, bool) else None,
                "point": point,
            }
        )
    title = name or str(payload.get("fixtureId") or "") or "Imported"
    return _from_rows(rows, name=title, controller_id=controller_id, output_index=output_index)


def _from_triples(payload: list, *, name: str, controller_id: str, output_index: int) -> dict:
    rows: list[dict] = []
    for item in payload:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise ValueError("A Pixelblaze map is a JSON array of [x, y, z].")
        try:
            point = (float(item[0]), float(item[1]), float(item[2]))
        except (TypeError, ValueError) as exc:
            raise ValueError("A Pixelblaze map is a JSON array of [x, y, z].") from exc
        rows.append({"fixture": "", "controller": "", "output": None, "index": None, "point": point})
    if not rows:
        raise ValueError("That map has no pixels.")
    return _from_rows(rows, name=name, controller_id=controller_id, output_index=output_index)


def _from_rows(rows: list[dict], *, name: str, controller_id: str, output_index: int) -> dict:
    groups: dict[tuple[str, int], list[dict]] = {}
    order: list[tuple[str, int]] = []
    fixture_name = name
    for row in rows:
        controller = str(row.get("controller") or controller_id or "imported")
        output = row.get("output")
        output_value = int(output) if isinstance(output, int) else int(output_index)
        key = (controller, output_value)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
        if not fixture_name and row.get("fixture"):
            fixture_name = str(row["fixture"])
    runs = []
    waypoints = []
    for controller, output in order:
        grouped = groups[(controller, output)]
        grouped.sort(key=lambda item: (item.get("index") is None, item.get("index") if item.get("index") is not None else 0))
        indexes = [item["index"] for item in grouped if isinstance(item.get("index"), int)]
        start = min(indexes) if indexes else 0
        run_waypoints, segments = _exact_pixels([item["point"] for item in grouped])
        waypoints.extend(run_waypoints)
        runs.append(
            _run(
                controller_id=controller,
                output_index=output,
                start_index=start,
                pixel_count=len(grouped),
                pitch_mm=CELL_MM,
                segments=segments,
            )
        )
    return _fixture(fixture_name or "Imported", "freeform", waypoints, runs)


def _exact_pixels(points: list[tuple[float, float, float]]) -> tuple[list[dict], list[dict]]:
    """One waypoint per pixel. The segment names that point twice so any fraction lands on it."""
    waypoints = []
    segments = []
    for point in points:
        waypoint_id = str(uuid.uuid4())
        waypoints.append(_waypoint(waypoint_id, point, "imported"))
        segments.append(
            {
                "id": str(uuid.uuid4()),
                "kind": "straight",
                "waypointIds": [waypoint_id, waypoint_id],
                "params": {},
                "pixelCountOverride": 1,
            }
        )
    return waypoints, segments


def _from_xmodel(text: str, *, name: str, controller_id: str, output_index: int) -> dict:
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("That model file is not a usable xLights model.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError("That xLights file could not be read.") from exc
    nodes = _model_nodes(root)
    if not nodes:
        raise ValueError("That xLights file has no model.")
    runs = []
    waypoints: list[dict] = []
    title = name
    planar = True
    for node in nodes:
        display = (node.attrib.get("DisplayAs") or "").strip().lower()
        tag = _local(node.tag)
        if tag == "polylinemodel" or display.replace(" ", "") == "polyline":
            built = _poly_run(node, controller_id, output_index)
            planar = planar and built[2]
        elif tag == "custommodel" or display in {"", "custom"}:
            built = _custom_run(node, controller_id, output_index)
            planar = planar and built[2]
        else:
            label = node.attrib.get("DisplayAs") or tag
            raise ValueError(f"This xLights model is a {label}. Import a Custom model or a Poly Line.")
        run_waypoints, run, flat = built
        waypoints.extend(run_waypoints)
        runs.append(run)
        if not title:
            title = (node.attrib.get("name") or "").strip()
    hint = "planar" if planar else "freeform"
    return _fixture(title or "Imported", hint, waypoints, runs)


def _model_nodes(root: ET.Element) -> list[ET.Element]:
    tag = _local(root.tag)
    if tag == "models":
        return [child for child in list(root) if _local(child.tag) == "model"]
    if tag in {"custommodel", "polylinemodel", "model"}:
        return [root]
    return []


def _local(tag: str) -> str:
    if "}" in tag:
        tag = tag.split("}", 1)[1]
    return tag.lower()


def _poly_run(node: ET.Element, controller_id: str, output_index: int) -> tuple[list[dict], dict, bool]:
    count_points = _int_attr(node, "NumPoints", 0)
    numbers = _floats(node.attrib.get("PointData") or "")
    if count_points >= 2:
        numbers = numbers[: count_points * 3]
    if len(numbers) < 6 or len(numbers) % 3 != 0:
        raise ValueError("This poly line has no corner points.")
    points = [Vec3(numbers[index], numbers[index + 1], numbers[index + 2]) for index in range(0, len(numbers), 3)]
    edges = max(len(points) - 1, 1)
    segment_counts = _segment_counts(node, edges)
    total = sum(segment_counts) if segment_counts is not None else _int_attr(node, "NodesPerString", 0)
    if total < 1:
        raise ValueError("This poly line has no pixel count.")
    pitch = _float_attr(node, "WledPitch")
    length = polyline_length(points)
    if length <= 1e-9:
        raise ValueError("This poly line has no length.")
    if pitch is None or pitch <= 0 or pitch > 1000:
        pitch = length / total * 1000.0
        if pitch > 1000:
            scale = (total * CELL_MM / 1000.0) / length
            points = [point * scale for point in points]
            pitch = CELL_MM
            length = polyline_length(points)
    flat = max(point.z for point in points) - min(point.z for point in points) < 1e-4
    waypoints = [_waypoint(str(uuid.uuid4()), (point.x, point.y, point.z), "imported") for point in points]
    if segment_counts is None:
        kind = "straight" if len(waypoints) == 2 else "polyline"
        segments = [
            {
                "id": str(uuid.uuid4()),
                "kind": kind,
                "waypointIds": [item["id"] for item in waypoints],
                "params": {},
            }
        ]
        pixel_count = total
    else:
        segments = []
        for index, count in enumerate(segment_counts):
            kind = "jump" if count == 0 else "straight"
            segment: dict = {
                "id": str(uuid.uuid4()),
                "kind": kind,
                "waypointIds": [waypoints[index]["id"], waypoints[index + 1]["id"]],
                "params": {},
            }
            if count > 0:
                segment["pixelCountOverride"] = count
            segments.append(segment)
        pixel_count = sum(segment_counts)
    controller = (node.attrib.get("Controller") or "").strip() or controller_id or "imported"
    output = _int_attr(node, "WledOutput", output_index)
    start = _int_attr(node, "WledStart", 0)
    run = _run(controller, output, start, pixel_count, pitch, segments)
    return waypoints, run, flat


def _segment_counts(node: ET.Element, edges: int) -> list[int] | None:
    if "Seg1" not in node.attrib:
        return None
    counts = []
    for index in range(edges):
        counts.append(_int_attr(node, f"Seg{index + 1}", 0))
    return counts


def _custom_run(node: ET.Element, controller_id: str, output_index: int) -> tuple[list[dict], dict, bool]:
    width = _dimension(node, "CustomWidth", "parm1")
    height = _dimension(node, "CustomHeight", "parm2")
    depth = _int_attr(node, "Depth", 1)
    if width < 1 or height < 1 or depth < 1:
        raise ValueError("This custom model has no grid size.")
    cells = _parse_custom_cells(node, width, height, depth)
    occupied = [
        (layer, row, column, value)
        for layer, rows in enumerate(cells)
        for row, columns in enumerate(rows)
        for column, value in enumerate(columns)
        if value >= 1
    ]
    if not occupied:
        raise ValueError("This custom model has no pixels.")
    occupied.sort(key=lambda item: item[3])
    cell_x, cell_y, cell_z = _cell_metres(node, width, height, depth)
    points = []
    for layer, row, column, _value in occupied:
        points.append((column * cell_x, row * cell_y, layer * cell_z))
    waypoints, segments = _exact_pixels(points)
    controller = (node.attrib.get("Controller") or "").strip() or controller_id or "imported"
    output = _int_attr(node, "WledOutput", output_index)
    run = _run(controller, output, 0, len(points), CELL_MM, segments)
    return waypoints, run, depth == 1


def _dimension(node: ET.Element, name: str, legacy: str) -> int:
    if node.attrib.get(name):
        return _int_attr(node, name, 0)
    return _int_attr(node, legacy, 0)


def _parse_custom_cells(node: ET.Element, width: int, height: int, depth: int) -> list[list[list[int]]]:
    compressed = (node.attrib.get("CustomModelCompressed") or "").strip()
    if compressed:
        return _parse_compressed(compressed, width, height, depth)
    custom = node.attrib.get("CustomModel") or ""
    if not custom.strip():
        raise ValueError("This custom model has no pixel grid.")
    return _parse_custom_model(custom, width, height, depth)


def _parse_compressed(text: str, width: int, height: int, depth: int) -> list[list[list[int]]]:
    cells = [[[-1 for _ in range(width)] for _ in range(height)] for _ in range(depth)]
    for item in text.split(";"):
        if not item.strip():
            continue
        parts = [part.strip() for part in item.split(",")]
        if len(parts) not in {3, 4}:
            continue
        try:
            node = int(parts[0])
            row = int(parts[1])
            column = int(parts[2])
            layer = int(parts[3]) if len(parts) == 4 else 0
        except ValueError:
            continue
        if node < 1 or row < 0 or column < 0 or layer < 0:
            continue
        if layer >= depth or row >= height or column >= width:
            continue
        cells[layer][row][column] = node
    return cells


def _parse_custom_model(text: str, width: int, height: int, depth: int) -> list[list[list[int]]]:
    layers = text.split("|")
    cells = [[[-1 for _ in range(width)] for _ in range(height)] for _ in range(max(depth, len(layers)))[:depth]]
    for layer_index, layer in enumerate(layers[:depth]):
        for row_index, row in enumerate(layer.split(";")[:height]):
            for column_index, value in enumerate(row.split(",")[:width]):
                token = value.strip()
                if not token:
                    continue
                try:
                    number = int(token)
                except ValueError:
                    continue
                if number >= 1:
                    cells[layer_index][row_index][column_index] = number
    return cells


def _cell_metres(node: ET.Element, width: int, height: int, depth: int) -> tuple[float, float, float]:
    width_mm = _float_attr(node, "widthmm")
    height_mm = _float_attr(node, "heightmm")
    depth_mm = _float_attr(node, "depthmm")
    cell_x = (width_mm / width) / 1000.0 if width_mm and width_mm > 0 else CELL_MM / 1000.0
    cell_y = (height_mm / height) / 1000.0 if height_mm and height_mm > 0 else CELL_MM / 1000.0
    cell_z = (depth_mm / depth) / 1000.0 if depth_mm and depth_mm > 0 and depth > 1 else CELL_MM / 1000.0
    return cell_x, cell_y, cell_z


def _floats(text: str) -> list[float]:
    values = []
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            values.append(float(token))
        except ValueError as exc:
            raise ValueError("A poly line point is not a number.") from exc
    return values


def _int_attr(node: ET.Element, name: str, default: int) -> int:
    raw = node.attrib.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _float_attr(node: ET.Element, name: str) -> float | None:
    raw = node.attrib.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _fixture(name: str, hint: str, waypoints: list[dict], runs: list[dict]) -> dict:
    title = (name or "Imported").strip()[:80] or "Imported"
    return {
        "schemaVersion": 1,
        "id": str(uuid.uuid4()),
        "name": title,
        "origin": {"position": [0.0, 0.0, 0.0], "orientation": [0.0, 0.0, 0.0, 1.0]},
        "surfaceHint": hint if hint in {"planar", "cylindrical", "branching", "freeform"} else "freeform",
        "waypoints": waypoints,
        "runs": runs,
    }


def _run(
    controller_id: str,
    output_index: int,
    start_index: int,
    pixel_count: int,
    pitch_mm: float,
    segments: list[dict],
) -> dict:
    pitch = float(pitch_mm)
    if pitch <= 0 or pitch > 1000:
        pitch = CELL_MM
    return {
        "id": str(uuid.uuid4()),
        "controllerId": (controller_id or "imported")[:80] or "imported",
        "outputIndex": max(0, int(output_index)),
        "startIndex": max(0, int(start_index)),
        "pixelCount": max(0, int(pixel_count)),
        "pitchMm": pitch,
        "colorOrder": "GRB",
        "segments": segments,
    }


def _waypoint(waypoint_id: str, point: tuple[float, float, float], source: str) -> dict:
    return {
        "id": waypoint_id,
        "position": [float(point[0]), float(point[1]), float(point[2])],
        "source": source,
        "confidence": 1.0,
        "label": None,
    }
