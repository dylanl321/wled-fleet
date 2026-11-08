"""Detection, import, alignment, branch effects, and the later regression fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.mapper.align import align_points, distribute_drift
from app.mapper.compile import compile_fixture
from app.mapper.detect_math import (
    CameraIntrinsics,
    CameraPose,
    bit_width,
    code_frames,
    ddp_index,
    Ray,
    ray_from_pixel,
    refine_on_model,
    room_brightness,
    triangulate,
)
from app.mapper.effects import effect_colors
from app.mapper.export import pixelblaze_map, pixels_csv, xlights_polyline, xlights_xmodel
from app.mapper.geom import Vec3
from app.mapper.importers import import_text
from app.mapper.schema_check import require_valid
from tests.test_mapper_api import _app

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def load(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


def test_phase_fixtures_compile():
    for name in ("helix-taps.json", "detected-pixel.json"):
        fixture = load(name)
        require_valid(fixture)
        pixels = compile_fixture(fixture)
        assert len(pixels) == fixture["runs"][0]["pixelCount"]
    helix = compile_fixture(load("helix-taps.json"))
    assert abs(helix[0].x - 0.2) < 1e-6
    assert abs(helix[0].y) < 1e-6
    detected = compile_fixture(load("detected-pixel.json"))
    assert abs(detected[3].x - 0.04) < 1e-9
    assert abs(detected[3].y - 0.05) < 1e-9
    assert abs(detected[2].x) < 1e-9


def test_binary_plan_and_dark_room():
    assert bit_width(60) == 6
    assert bit_width(1) == 0
    frames = code_frames([0, 1, 2, 3], "binary")
    bits = [frame for frame in frames if frame["role"] == "bit"]
    assert bits[0]["lit"] == [1, 3]
    assert bits[1]["lit"] == [2, 3]
    singles = code_frames([0, 1, 2], "sequential")
    assert [frame["lit"] for frame in singles if frame["role"] == "single"] == [[0], [1], [2]]
    assert ddp_index(4, 100) == 104
    assert room_brightness([12] * 20).dark is True
    assert room_brightness([80] * 20).dark is False


def test_ray_refine_and_triangulate():
    pose = CameraPose(Vec3(1, 0, 0), Vec3(0, 1, 0), Vec3(0, 0, 1), Vec3(0, 0, 0))
    lens = CameraIntrinsics(200, 200, 100, 50)
    ray = ray_from_pixel(100, 50, lens, pose)
    assert abs(ray.direction.x) < 1e-6
    assert ray.direction.z < 0
    hit = refine_on_model(ray, [("seg", Vec3(0, 0, -2)), ("seg", Vec3(0, 1, -2))], 0.05)
    assert hit is not None
    assert abs(hit.position.z + 2) < 1e-6
    assert refine_on_model(ray, [("seg", Vec3(1, 0, -2)), ("seg", Vec3(1, 1, -2))], 0.05) is None
    side = Ray(Vec3(1, 0, -1), Vec3(-1, 0, 0))
    found = triangulate(ray, side)
    assert found is not None
    assert abs(found.position.x) < 1e-6 and abs(found.position.z + 1) < 1e-6


def test_import_round_trip_and_polyline():
    straight = load("straight-1m.json")
    pixels = compile_fixture(straight)
    csv_back = import_text(pixels_csv(pixels), filename="straight.csv", name="Straight again")
    blaze_back = import_text(pixelblaze_map(pixels), filename="straight.json", name="Blaze again")
    for imported in (csv_back, blaze_back):
        compiled = compile_fixture(imported)
        assert len(compiled) == len(pixels)
        for original, again in zip(pixels, compiled):
            assert abs(original.x - again.x) < 1e-3
            assert abs(original.y - again.y) < 1e-3
            assert abs(original.z - again.z) < 1e-3
    model = xlights_xmodel("Straight 1m", pixels, surface_hint="freeform", width=1, height=60, depth=2)
    assert 'SourceVersion="2026.19"' in model
    assert 'CustomWidth="1"' in model
    assert 'Depth="2"' in model
    imported_model = import_text(model, filename="straight.xmodel", name="Grid")
    recovered = compile_fixture(imported_model)
    assert {pixel.index for pixel in recovered} == set(range(len(pixels)))
    wall = load("wall-outline.json")
    polyline = xlights_polyline(wall["name"], wall)
    assert 'DisplayAs="Poly Line"' in polyline
    assert f'NumPoints="{len(wall["waypoints"])}"' in polyline
    assert "1.000000,0.000000,0.000000" in polyline
    assert 'SourceVersion="2026.19"' in polyline


def test_drift_alignment_and_branch_colors():
    points = [Vec3(0, 0, 0), Vec3(0, 1, 0), Vec3(0.3, 2, 0)]
    closed = distribute_drift(points, Vec3(0.3, 2, 0))
    assert closed.corrected[0] == points[0]
    assert abs(closed.corrected[-1].x) < 1e-6
    rigid = align_points([Vec3(0, 0, 0), Vec3(0, 0, 1)], [Vec3(1, 0, 0), Vec3(2, 0, 0)])
    moved = rigid.apply(Vec3(0, 0, 1))
    assert abs(moved.x - 2) < 1e-6
    assert abs(moved.z) < 1e-6
    tree = load("branching-tree.json")
    pixels = compile_fixture(tree)
    colors = effect_colors(pixels, "fill-bottom", 1.0, {"target": "branch-1"})
    branch = [index for index, pixel in enumerate(pixels) if pixel.segment == "seg-branch-1"]
    trunk = [index for index, pixel in enumerate(pixels) if pixel.segment == "seg-trunk"]
    assert branch and all(colors[index] == (255, 244, 230) for index in branch)
    assert trunk and colors[trunk[0]] == (0, 0, 0)
    band = effect_colors(pixels, "fill-bottom", 1.0, {"heightM": 1.4, "bandM": 0.05})
    assert any(color != (0, 0, 0) for color in band)
    assert band[0] == (0, 0, 0)


def test_detect_import_and_align_routes(tmp_path):
    app = _app(tmp_path)
    straight = load("straight-1m.json")
    with TestClient(app) as client:
        assert client.post("/api/mapper/fixtures", json=straight).status_code == 200
        missing = client.post(f"/api/mapper/fixtures/{straight['id']}/detect", json={})
        assert missing.status_code == 400
        assert ddp_index(0, 120) == 120
        csv_body = client.get(f"/api/mapper/fixtures/{straight['id']}/export?format=csv").text
        imported = client.post("/api/mapper/import", json={"filename": "from.csv", "name": "From csv", "text": csv_body})
        assert imported.status_code == 200
        assert imported.json()["name"] == "From csv"
        polyline = client.get(f"/api/mapper/fixtures/{straight['id']}/export?format=polyline")
        assert polyline.status_code == 200
        assert 'DisplayAs="Poly Line"' in polyline.text
        saved = client.get(f"/api/mapper/fixtures/{straight['id']}").json()
        ends = [Vec3(*item["position"]) for item in saved["waypoints"]]
        closed = distribute_drift(ends, ends[-1] + Vec3(0.1, 0, 0))
        assert closed.corrected[0] == ends[0]
        assert closed.corrected[-1].x < ends[-1].x
