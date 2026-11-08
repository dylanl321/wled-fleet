"""Pixel-map math against the spec's three regression fixtures."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.mapper.compile import compile_fixture, validate_counts
from app.mapper.ddp import DDP_MAX_PIXELS, build_packets
from app.mapper.effects import effect_colors
from app.mapper.export import XLIGHTS_SOURCE_VERSION, pixelblaze_map, pixels_csv, xlights_polyline, xlights_xmodel
from app.mapper.geom import (
    Vec3,
    catmull_rom,
    curve_length,
    curve_position,
    distance,
    fit_circle,
    fit_helix,
    helix_length,
    helix_position,
    normalise_points,
    nudge_toward_camera,
    robust_circle,
)
from app.mapper.ledmap import grid_map, linear_by_height
from app.mapper.schema_check import require_valid, validate

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_regression_fixtures_match_the_schema():
    for name in (
        "straight-1m.json",
        "wall-outline.json",
        "branching-tree.json",
        "two-controllers.json",
        "helix-taps.json",
        "detected-pixel.json",
    ):
        require_valid(load(name))


def test_schema_rejects_a_broken_fixture():
    fixture = load("straight-1m.json")
    fixture["surfaceHint"] = "mesh"
    errors = validate(fixture)
    assert any("surfaceHint" in error for error in errors)
    fixture = load("straight-1m.json")
    fixture["extra"] = True
    errors = validate(fixture)
    assert any("extra" in error for error in errors)


def test_straight_metre_places_sixty_pixels():
    pixels = compile_fixture(load("straight-1m.json"))
    assert len(pixels) == 60
    assert (pixels[0].x, pixels[0].y, pixels[0].z) == pytest.approx((0, 0, 0))
    assert (pixels[30].x, pixels[30].y, pixels[30].z) == pytest.approx((0, 0.5, 0))
    assert pixels[59].y == pytest.approx(59 / 60)
    assert pixels[0].index == 0
    assert pixels[59].index == 59
    assert validate_counts(load("straight-1m.json"), pixels)[0]["ok"] is True


def test_wall_outline_keeps_spacing_across_corners():
    pixels = compile_fixture(load("wall-outline.json"))
    assert len(pixels) == 180
    assert (pixels[0].x, pixels[0].y) == pytest.approx((0, 0))
    assert (pixels[60].x, pixels[60].y) == pytest.approx((1, 0))
    assert (pixels[90].x, pixels[90].y) == pytest.approx((1, 0.5))
    assert (pixels[150].x, pixels[150].y) == pytest.approx((0, 0.5))
    assert pixels[60].u == pytest.approx(1 / 3)
    assert all(pixel.controller == "aa:bb:cc:dd:ee:02" for pixel in pixels)


def test_tree_helix_and_branches_on_two_outputs():
    fixture = load("branching-tree.json")
    pixels = compile_fixture(fixture)
    assert {pixel.controller for pixel in pixels} == {"d1:67:00:00:00:07"}
    trunk = [pixel for pixel in pixels if pixel.output == 0]
    branches = [pixel for pixel in pixels if pixel.output == 1]
    assert len(trunk) == 141
    assert len(branches) == 102
    assert (trunk[0].x, trunk[0].y, trunk[0].z) == pytest.approx((0.08, 0, 0))
    assert trunk[-1].y < 1.2
    assert branches[0].segment == "seg-branch-1"
    assert (branches[0].x, branches[0].y, branches[0].z) == pytest.approx((0, 1.2, 0))
    assert branches[42].segment == "seg-branch-2"
    assert (branches[42].x, branches[42].y, branches[42].z) == pytest.approx((-0.8, 1.8, 0))
    reports = {item["runId"]: item for item in validate_counts(fixture, pixels)}
    assert reports["run-trunk"]["ok"] is True
    assert reports["run-branches"]["ok"] is True
    assert reports["run-branches"]["placed"] == 102


def _two_metre_strip(override: int) -> dict:
    fixture = load("straight-1m.json")
    fixture["waypoints"].append(
        {
            "id": "11111111-1111-4111-8111-000000000003",
            "position": [0, 2, 0],
            "source": "typed",
            "confidence": 1,
            "label": "Top",
        }
    )
    run = fixture["runs"][0]
    run["segments"][0]["pixelCountOverride"] = override
    run["segments"].append(
        {
            "id": "upper",
            "kind": "straight",
            "waypointIds": [
                "11111111-1111-4111-8111-000000000002",
                "11111111-1111-4111-8111-000000000003",
            ],
            "params": {},
        }
    )
    run["pixelCount"] = override + 60
    return fixture


def test_pixel_count_override_does_not_shift_the_next_segment():
    for override in (10, 100):
        pixels = compile_fixture(_two_metre_strip(override))
        first = [pixel for pixel in pixels if pixel.segment == "seg-straight"]
        upper = [pixel for pixel in pixels if pixel.segment == "upper"]
        assert len(first) == override
        assert len(upper) == 60
        assert first[0].y == pytest.approx(0)
        assert first[-1].y == pytest.approx((override - 1) / override)
        assert upper[0].y == pytest.approx(1)
        assert upper[1].y == pytest.approx(1 + 1 / 60)
        assert upper[59].y == pytest.approx(1 + 59 / 60)
        assert [pixel.index for pixel in first] == list(range(override))
        assert [pixel.index for pixel in upper] == list(range(override, override + 60))
        heights = [pixel.y for pixel in upper]
        assert heights == sorted(heights)
        assert len(set(round(value, 6) for value in heights)) == 60


def test_two_outputs_keep_separate_addresses():
    from app.mapper.address import absolute_pixels, convert_start_indices

    pixels = compile_fixture(load("branching-tree.json"))
    assert min(pixel.index for pixel in pixels if pixel.output == 1) == 0
    controllers = [
        {
            "id": "d1:67:00:00:00:07",
            "outputs": [
                {"index": 0, "start": 0, "pixelCount": 141},
                {"index": 1, "start": 141, "pixelCount": 102},
            ],
        }
    ]
    absolute = absolute_pixels(pixels, controllers)
    assert len({pixel.index for pixel in absolute}) == len(absolute)
    assert min(pixel.index for pixel in absolute if pixel.output == 1) == 141
    stacked = absolute_pixels(pixels, [])
    assert min(pixel.index for pixel in stacked if pixel.output == 1) == 141

    old = {
        "runs": [
            {"controllerId": "d1:67:00:00:00:07", "outputIndex": 1, "startIndex": 141},
            {"controllerId": "d1:67:00:00:00:07", "outputIndex": 0, "startIndex": 0},
        ]
    }
    converted = convert_start_indices(old, controllers)
    assert converted["runs"][0]["startIndex"] == 0
    assert converted["runs"][1]["startIndex"] == 0
    assert convert_start_indices(converted, controllers) is None


def test_jump_does_not_consume_pixel_indices():
    fixture = load("straight-1m.json")
    run = fixture["runs"][0]
    run["segments"] = [
        run["segments"][0],
        {
            "id": "gap",
            "kind": "jump",
            "waypointIds": [
                "11111111-1111-4111-8111-000000000002",
                "11111111-1111-4111-8111-000000000001",
            ],
            "params": {},
        },
        {
            "id": "back",
            "kind": "straight",
            "waypointIds": [
                "11111111-1111-4111-8111-000000000002",
                "11111111-1111-4111-8111-000000000001",
            ],
            "params": {},
        },
    ]
    run["pixelCount"] = 120
    pixels = compile_fixture(fixture)
    assert len(pixels) == 120
    assert pixels[60].segment == "back"
    assert pixels[60].y == pytest.approx(1)


def test_helix_formula_and_handedness():
    rise = 1.2
    radius = 0.08
    turns = 4
    expected = turns * math.sqrt((2 * math.pi * radius) ** 2 + (rise / turns) ** 2)
    assert helix_length(turns, radius, rise) == pytest.approx(expected)
    start, end = Vec3(0, 0, 0), Vec3(0, rise, 0)
    quarter = helix_position(start, end, radius, radius, turns, 0.25, "ccw")
    assert (quarter.x, quarter.y, quarter.z) == pytest.approx((0.08, 0.3, 0))
    ccw = helix_position(start, end, radius, radius, turns, 0.01, "ccw")
    cw = helix_position(start, end, radius, radius, turns, 0.01, "cw")
    # Looking up the trunk (gaze +Y), +X toward -Z is counter-clockwise.
    # The same motion is clockwise when viewed from above. ccw keeps the written formula.
    assert ccw.x > 0 and ccw.z < 0
    assert cw.z > 0
    assert ccw.z == pytest.approx(-cw.z)


def test_helix_fit_recovers_a_cylinder_and_collapses_a_line():
    taps = []
    for index, angle in enumerate((0, math.pi / 2, math.pi, 3 * math.pi / 2)):
        taps.append(Vec3(0.1 * math.cos(angle), index * 0.3, 0.1 * math.sin(angle)))
    fitted = fit_helix(taps)
    assert fitted.turns == 3
    assert fitted.axis_start.x == pytest.approx(0, abs=1e-6)
    assert fitted.axis_start.z == pytest.approx(0, abs=1e-6)
    assert fitted.radius_start_m == pytest.approx(0.1, abs=1e-6)
    taps = [Vec3(0.2, y, 0.0) for y in (0, 0.4, 0.8, 1.2)]
    bare = fit_helix(taps)
    assert bare.turns == 3
    assert bare.radius_start_m == pytest.approx(0, abs=1e-6)
    line = fit_helix(taps, radius_m=0.08, inward=Vec3(-1, 0, 0))
    assert line.turns == 3
    assert line.radius_start_m == pytest.approx(0.08)
    assert line.axis_start.x == pytest.approx(0.12)
    assert line.axis_start.z == pytest.approx(0, abs=1e-6)
    assert line.axis_end.y == pytest.approx(1.2)


def test_curve_passes_through_its_ends_and_is_longer_than_the_chord():
    points = [Vec3(0, 0, 0), Vec3(0, 0.2, 0.4), Vec3(0, 0.6, -0.2), Vec3(0, 1, 0)]
    start = curve_position(points, 0)
    end = curve_position(points, 1)
    assert (start.x, start.y, start.z) == pytest.approx((0, 0, 0))
    assert (end.x, end.y, end.z) == pytest.approx((0, 1, 0))
    assert curve_length(points) > distance(points[0], points[-1])
    knot = catmull_rom(points[0], points[0], points[1], points[2], 0)
    assert (knot.x, knot.y, knot.z) == pytest.approx((0, 0, 0))


def test_normalise_puts_the_first_pixel_on_plus_z():
    moved, frame = normalise_points([Vec3(1, 2, 3), Vec3(1, 5, 3), Vec3(3, 2, 3)])
    assert moved[0].y == pytest.approx(0)
    assert min(point.y for point in moved) == pytest.approx(0)
    centroid_x = sum(point.x for point in moved) / 3
    centroid_z = sum(point.z for point in moved) / 3
    assert centroid_x == pytest.approx(0, abs=1e-9)
    assert centroid_z == pytest.approx(0, abs=1e-9)
    assert moved[0].x == pytest.approx(0, abs=1e-9)
    assert moved[0].z > 0
    assert frame.translation.y == pytest.approx(-2)


def test_circle_fit_and_depth_nudge():
    ring = []
    for step in range(16):
        angle = step / 16 * math.tau
        ring.append((0.08 * math.cos(angle), 0.08 * math.sin(angle)))
    wall = [(3.0, z * 0.05) for z in range(-8, 9)]
    center_x, center_z, radius = robust_circle(ring + wall, near=(0.08, 0.0), max_distance=0.5)
    assert center_x == pytest.approx(0, abs=1e-3)
    assert center_z == pytest.approx(0, abs=1e-3)
    assert radius == pytest.approx(0.08, abs=1e-3)
    center_x, center_z, radius = fit_circle([(1.08, 2), (1, 2.08), (0.92, 2), (1, 1.92)])
    assert center_x == pytest.approx(1, abs=1e-6)
    assert center_z == pytest.approx(2, abs=1e-6)
    assert radius == pytest.approx(0.08, abs=1e-6)
    nudged = nudge_toward_camera(Vec3(0, 0, 2), Vec3(0, 0, 0), 0.4)
    assert nudged.z == pytest.approx(1.6)


def test_ledmap_sorts_by_height_and_grid_keeps_gaps():
    straight = compile_fixture(load("straight-1m.json"))
    height_map = linear_by_height(straight)
    assert height_map["map"] == list(range(60))
    grid = grid_map(straight, width=10, height=6, surface_hint="planar")
    assert grid["width"] == 10 and grid["height"] == 6
    assert len(grid["map"]) == 60
    assert sorted(index for index in grid["map"] if index >= 0) == list(range(60))
    sparse = grid_map(straight[:4], width=4, height=3, surface_hint="planar")
    assert -1 in sparse["map"]
    assert len(sparse["map"]) == 12


def test_folded_strip_ledmap_runs_bottom_to_top():
    fixture = load("straight-1m.json")
    fixture["runs"][0]["segments"].append(
        {
            "id": "down",
            "kind": "straight",
            "waypointIds": [
                "11111111-1111-4111-8111-000000000002",
                "11111111-1111-4111-8111-000000000001",
            ],
            "params": {},
        }
    )
    fixture["runs"][0]["pixelCount"] = 120
    pixels = compile_fixture(fixture)
    ordered = linear_by_height(pixels)["map"]
    by_index = {pixel.index: pixel for pixel in pixels}
    heights = [by_index[index].y for index in ordered]
    assert heights == sorted(heights)
    assert ordered[0] == 0


def test_exports_and_ddp_chunking():
    pixels = compile_fixture(load("straight-1m.json"))
    csv_text = pixels_csv(pixels)
    assert csv_text.splitlines()[0] == "fixture,controller,output,index,x_m,y_m,z_m,segment,u"
    assert len(csv_text.splitlines()) == 61
    blaze = json.loads(pixelblaze_map(pixels))
    assert blaze[0] == [0, 0, 0]
    assert len(blaze) == 60
    model = xlights_xmodel("Straight 1m", pixels, surface_hint="freeform", width=10, height=6)
    assert model.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert '<models type="exported">' in model
    assert 'DisplayAs="Custom"' in model
    assert f'SourceVersion="{XLIGHTS_SOURCE_VERSION}"' in model
    assert 'CustomWidth="10"' in model
    assert 'CustomHeight="6"' in model
    assert "parm1" not in model and "parm2" not in model
    assert 'StringType="RGB Nodes"' in model
    assert 'PixelCount="60"' in model
    assert 'CustomModel="' in model
    assert "CustomModelCompressed" not in model
    assert ",0," not in model
    assert model.count("|") == 0
    thick = xlights_xmodel("Tree", pixels, surface_hint="freeform", width=10, height=6, depth=2)
    assert 'Depth="2"' in thick
    assert 'CustomModelCompressed="' in thick
    assert any(len(part.split(",")) == 4 for part in thick.split('CustomModelCompressed="', 1)[1].split('"', 1)[0].split(";"))
    line = xlights_polyline("Straight 1m", load("straight-1m.json"))
    assert 'DisplayAs="Poly Line"' in line
    assert 'NumPoints="2"' in line
    assert 'NodesPerString="60"' in line
    assert f'SourceVersion="{XLIGHTS_SOURCE_VERSION}"' in line
    colors = bytes([index % 256 for index in range(10 * 3)])
    one = build_packets(colors, sequence=1)
    assert one[0][:10] == bytes([0x41, 1, 0x0B, 1, 0, 0, 0, 0, 0, 30])
    wide = bytes([7]) * (DDP_MAX_PIXELS + 1) * 3
    packets = build_packets(wide, sequence=4)
    assert len(packets) == 2
    assert packets[0][0] == 0x40
    assert packets[1][0] == 0x41
    assert int.from_bytes(packets[0][8:10], "big") == DDP_MAX_PIXELS * 3
    assert int.from_bytes(packets[1][4:8], "big") == DDP_MAX_PIXELS * 3
    assert int.from_bytes(packets[1][8:10], "big") == 3
    assert packets[0][1] == 4
    assert packets[1][1] == 5


def test_fill_lights_from_the_bottom():
    pixels = compile_fixture(load("straight-1m.json"))
    dark = effect_colors(pixels, "fill-bottom", 0)
    assert dark[0] == (255, 244, 230)
    assert dark[10] == (0, 0, 0)
    full = effect_colors(pixels, "fill-bottom", 1)
    assert all(color == (255, 244, 230) for color in full)
    rainbow = effect_colors(pixels, "height-rainbow", 0)
    spin = effect_colors(pixels, "angle-spin", 0.25)
    assert rainbow[0] != rainbow[len(pixels) // 2]
    assert spin[0] != (0, 0, 0)
