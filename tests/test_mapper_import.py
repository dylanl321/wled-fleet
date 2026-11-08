"""Round-trip the exporters, and read the xLights shapes the 2026 serializer writes.

No .xmodel saved by xLights 2026.04 or 2026.19 was available. The samples below
follow CustomModel::ToCompressed, ParseCustomModel, and DeserializePolyLine
in the xLights source. They are not files the xLights app wrote.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.mapper.compile import compile_fixture
from app.mapper.export import pixelblaze_map, pixels_csv, pixels_json, xlights_polyline, xlights_xmodel
from app.mapper.importers import import_text
from app.mapper.schema_check import require_valid

ROOT = Path(__file__).resolve().parents[1]


def load(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


def _close(left: list, right: list, tol: float = 1e-5) -> None:
    assert len(left) == len(right)
    for a, b in zip(left, right):
        assert a.x == pytest.approx(b.x, abs=tol)
        assert a.y == pytest.approx(b.y, abs=tol)
        assert a.z == pytest.approx(b.z, abs=tol)
        assert a.index == b.index
        assert a.controller == b.controller
        assert a.output == b.output


def test_csv_json_and_pixelblaze_round_trip_positions():
    fixture = load("straight-1m.json")
    original = compile_fixture(fixture)
    csv_fixture = import_text(pixels_csv(original), filename="straight.csv")
    json_fixture = import_text(pixels_json(fixture["id"], original), filename="straight.json")
    blaze_fixture = import_text(pixelblaze_map(original), filename="straight.json", controller_id=original[0].controller)
    require_valid(csv_fixture)
    require_valid(json_fixture)
    require_valid(blaze_fixture)
    _close(compile_fixture(csv_fixture), original)
    _close(compile_fixture(json_fixture), original)
    _close(compile_fixture(blaze_fixture), original, tol=1e-9)


def test_wall_polyline_round_trips_the_path():
    fixture = load("wall-outline.json")
    original = compile_fixture(fixture)
    exported = xlights_polyline(fixture["name"], fixture)
    imported = import_text(exported, filename="wall.xmodel")
    require_valid(imported)
    assert imported["runs"][0]["segments"][0]["kind"] == "polyline"
    assert len(imported["waypoints"]) == 5
    _close(compile_fixture(imported), original, tol=1e-6)


def test_straight_line_export_refuses_a_helix():
    with pytest.raises(ValueError, match="helix"):
        xlights_polyline("Front tree", load("branching-tree.json"))


def test_custom_grid_import_keeps_every_node_and_a_full_grid_is_stable():
    fixture = load("straight-1m.json")
    pixels = compile_fixture(fixture)
    exported = xlights_xmodel("Straight 1m", pixels, surface_hint="freeform", width=10, height=6, depth=2)
    imported = import_text(exported, filename="straight.xmodel")
    require_valid(imported)
    placed = compile_fixture(imported)
    assert len(placed) == 60
    assert max(pixel.z for pixel in placed) > min(pixel.z for pixel in placed)

    square = import_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<models type="exported"><model name="Square" DisplayAs="Custom" CustomWidth="2" CustomHeight="2" Depth="1" CustomModel="1,2;3,4" SourceVersion="2026.19"/></models>
""",
        filename="square.xmodel",
    )
    corners = compile_fixture(square)
    again = xlights_xmodel("Square", corners, surface_hint="planar", width=2, height=2, depth=1)
    assert _payload(again) == "1,2;3,4"


def test_legacy_custom_model_and_compressed_layers():
    legacy = """<?xml version="1.0" encoding="UTF-8"?>
<custommodel name="Old square" parm1="2" parm2="2" Depth="1" CustomModel="1,2;3,4" SourceVersion="2025.12"></custommodel>
"""
    old = import_text(legacy, filename="old.xmodel")
    require_valid(old)
    placed = compile_fixture(old)
    assert [pixel.index for pixel in placed] == [0, 1, 2, 3]
    assert placed[1].x > placed[0].x
    assert placed[2].y > placed[0].y

    compressed = """<?xml version="1.0" encoding="UTF-8"?>
<models type="exported">
  <model name="Sparse" DisplayAs="Custom" CustomWidth="4" CustomHeight="4" Depth="2" SourceVersion="2026.04" versionNumber="8" CustomModelCompressed="1,0,0;2,3,1,1" StringType="RGB Nodes"/>
</models>
"""
    thick = compile_fixture(import_text(compressed, filename="sparse.xmodel"))
    assert len(thick) == 2
    assert thick[0].y == pytest.approx(0)
    assert thick[0].z == pytest.approx(0)
    assert thick[1].z > thick[0].z
    assert thick[1].y > thick[0].y


def test_poly_line_segment_counts_are_kept():
    line = """<?xml version="1.0" encoding="UTF-8"?>
<models type="exported">
  <model name="L" DisplayAs="Poly Line" NumPoints="3" PointData="0.0,0.0,0.0,1.0,0.0,0.0,1.0,0.5,0.0" NodesPerString="8" Seg1="3" Seg2="5" SourceVersion="2026.19"/>
</models>
"""
    fixture = import_text(line, filename="l.xmodel")
    require_valid(fixture)
    segments = fixture["runs"][0]["segments"]
    assert [segment["kind"] for segment in segments] == ["straight", "straight"]
    assert [segment["pixelCountOverride"] for segment in segments] == [3, 5]
    assert len(compile_fixture(fixture)) == 8


def _payload(model: str) -> str:
    marker = 'CustomModel="'
    assert marker in model
    return model.split(marker, 1)[1].split('"', 1)[0]
