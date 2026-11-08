"""Detection, import, alignment, branch effects, and the later regression fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.mapper.align import align_references, distribute_drift
from app.mapper.compile import compile_fixture
from app.mapper.detect import (
    brightest_blobs,
    decode_bits,
    decode_stationary,
    detection_plan,
    frame_count,
    is_dark,
    missing_indices,
    ray_from_pixel,
    refine_with_model,
    triangulate,
)
from app.mapper.effects import targeted_colors
from app.mapper.export import pixelblaze_map, pixels_csv, xlights_polyline, xlights_xmodel
from app.mapper.geom import Vec3
from app.mapper.importers import import_csv, import_pixelblaze, import_xmodel
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


def test_binary_plan_and_stationary_decode():
    assert frame_count(60) == 6
    assert frame_count(1) == 1
    plan = detection_plan(4, "binary")
    assert plan == [[1, 3], [2, 3]]
    assert detection_plan(3, "single") == [[0], [1], [2]]
    assert decode_bits([True, False, True]) == 5
    frames = [
        [(10.0, 10.0), (40.0, 10.0)],
        [(20.0, 10.0), (40.0, 12.0)],
    ]
    found = {item["index"]: item for item in decode_stationary(frames, join=8)}
    assert set(found) == {1, 2, 3}
    assert abs(found[3]["u"] - 40) < 1e-6
    assert abs(found[3]["v"] - 11) < 1e-6
    assert missing_indices(4, set(found)) == [0]
    assert is_dark(12) is True
    assert is_dark(80) is False
    image = [0.0] * 25
    image[2] = 200
    image[22] = 180
    blobs = brightest_blobs(image, 5, 5, threshold=40, limit=4, radius=1)
    assert blobs == [(2.0, 0.0), (2.0, 4.0)]


def test_ray_refine_and_triangulate():
    origin, direction = ray_from_pixel(100, 50, intrinsics={"fx": 200, "fy": 200, "cx": 100, "cy": 50}, pose=IDENTITY)
    assert abs(direction.x) < 1e-9
    assert abs(direction.y) < 1e-9
    assert direction.z < 0
    point = refine_with_model(origin, direction, [(Vec3(0, 0, -2), Vec3(0, 1, -2))])
    assert point is not None
    assert abs(point.z + 2) < 1e-6
    assert refine_with_model(origin, direction, [(Vec3(1, 0, -2), Vec3(1, 1, -2))], limit=0.05) is None
    hit = triangulate(Vec3(0, 0, 0), Vec3(0, 0, -1), Vec3(1, 0, -1), Vec3(-1, 0, 0))
    assert abs(hit.x) < 1e-6 and abs(hit.y) < 1e-6 and abs(hit.z + 1) < 1e-6


def test_import_round_trip_and_polyline():
    straight = load("straight-1m.json")
    pixels = compile_fixture(straight)
    csv_back = import_csv(pixels_csv(pixels), "Straight again")
    blaze_back = import_pixelblaze(pixelblaze_map(pixels), "Blaze again")
    for imported in (csv_back, blaze_back):
        compiled = compile_fixture(imported)
        assert len(compiled) == len(pixels)
        for original, again in zip(pixels, compiled):
            assert abs(original.x - again.x) < 1e-3
            assert abs(original.y - again.y) < 1e-3
            assert abs(original.z - again.z) < 1e-3
    model = xlights_xmodel("Straight 1m", pixels, surface_hint="freeform", width=1, height=60, depth=2)
    assert 'SourceVersion="2025.12"' in model
    assert 'CustomWidth="1"' in model
    assert 'Depth="2"' in model
    imported_model = import_xmodel(model, "Grid")
    recovered = compile_fixture(imported_model)
    assert {pixel.index for pixel in recovered} == set(range(len(pixels)))
    wall = load("wall-outline.json")
    polyline = xlights_polyline(wall["name"], wall["waypoints"], wall["runs"][0]["pixelCount"])
    assert 'DisplayAs="Poly Line"' in polyline
    assert f'NumPoints="{len(wall["waypoints"])}"' in polyline
    assert "1.000000,0.000000,0.000000" in polyline
    assert 'SourceVersion="2025.12"' in polyline


def test_drift_alignment_and_branch_colors():
    points = [Vec3(0, 0, 0), Vec3(0, 1, 0), Vec3(0.3, 2, 0)]
    closed = distribute_drift(points, Vec3(0.3, 0, 0))
    assert closed[0] == points[0]
    assert abs(closed[-1].x) < 1e-9
    assert abs(closed[1].x + 0.15) < 1e-9
    moved, _yaw = align_references(Vec3(0, 0, 0), Vec3(0, 0, 1), Vec3(1, 0, 0), Vec3(2, 0, 0), [Vec3(0, 0, 1)])
    assert abs(moved[0].x - 2) < 1e-6
    assert abs(moved[0].z) < 1e-6
    tree = load("branching-tree.json")
    pixels = compile_fixture(tree)
    colors = targeted_colors(pixels, "fill-bottom", 1.0, {"branch": "branch 1"}, tree)
    branch = [index for index, pixel in enumerate(pixels) if pixel.segment == "seg-branch-1"]
    trunk = [index for index, pixel in enumerate(pixels) if pixel.segment == "seg-trunk"]
    assert branch and all(colors[index] == (255, 244, 230) for index in branch)
    assert trunk and colors[trunk[0]] == (0, 0, 0)
    band = targeted_colors(pixels, "fill-bottom", 1.0, {"height": 1.4, "band": 0.05}, tree)
    assert any(color != (0, 0, 0) for color in band)
    assert band[0] == (0, 0, 0)


def test_detect_import_and_align_routes(tmp_path):
    app = _app(tmp_path)
    straight = load("straight-1m.json")
    with TestClient(app) as client:
        assert client.post("/api/mapper/fixtures", json=straight).status_code == 200
        bright = client.post(f"/api/mapper/fixtures/{straight['id']}/detect/plan", json={"luma": 90})
        assert bright.status_code == 400
        plan = client.post(f"/api/mapper/fixtures/{straight['id']}/detect/plan", json={"mode": "binary", "luma": 10})
        assert plan.status_code == 200
        body = plan.json()
        assert body["pixelCount"] == 60
        assert len(body["frames"]) == 6
        shown = client.post(
            f"/api/mapper/fixtures/{straight['id']}/detect/frame",
            json={"pixels": body["frames"][0]},
        )
        assert shown.status_code == 200
        assert shown.json()["lit"] == len(body["frames"][0])
        accepted = client.post(
            f"/api/mapper/fixtures/{straight['id']}/detect/accept",
            json={"corrections": [{"ordinal": 4, "position": [0.02, 0.07, 0]}], "seen": [0, 1, 2]},
        )
        assert accepted.status_code == 200
        saved = accepted.json()
        assert saved["detection"]["needsViewpoint"] is True
        assert 4 in saved["detection"]["missing"]
        pixel = next(item for item in client.get(f"/api/mapper/fixtures/{straight['id']}/pixelmap").json()["pixels"] if item["index"] == 4)
        assert abs(pixel["x"] - 0.02) < 1e-9
        csv_body = client.get(f"/api/mapper/fixtures/{straight['id']}/export?format=csv").text
        imported = client.post("/api/mapper/import", json={"format": "csv", "name": "From csv", "text": csv_body})
        assert imported.status_code == 200
        assert imported.json()["name"] == "From csv"
        polyline = client.get(f"/api/mapper/fixtures/{straight['id']}/export?format=polyline")
        assert polyline.status_code == 200
        assert 'DisplayAs="Poly Line"' in polyline.text
        drifted = client.post(f"/api/mapper/fixtures/{straight['id']}/drift", json={"error": [0.1, 0, 0]})
        assert drifted.status_code == 200
        end = drifted.json()["waypoints"][1]["position"]
        assert abs(end[0] + 0.1) < 1e-9
        corrected = drifted.json()["runs"][0]["pixelCorrections"][0]["position"]
        assert abs(corrected[0] - (0.02 - 0.1 * 4 / 59)) < 1e-9
        aligned = client.post(
            f"/api/mapper/fixtures/{straight['id']}/align",
            json={"sourceA": [0, 0, 0], "sourceB": [0, 1, 0], "destA": [1, 0, 0], "destB": [1, 0, 1]},
        )
        assert aligned.status_code == 200
        assert abs(aligned.json()["waypoints"][0]["position"][0] - 1) < 1e-6
