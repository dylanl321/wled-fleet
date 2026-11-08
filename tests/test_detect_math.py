"""Synthetic frames and poses for camera pixel detection."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.mapper.compile import compile_fixture
from app.mapper.geom import Vec3
from app.mapper.detect_math import (
    ImagePoint,
    Ray,
    assess_pixels,
    bit_width,
    code_frames,
    ddp_index,
    decode_blobs,
    detect_blobs,
    downsample_max,
    full_resolution,
    ray_from_pixel,
    refine_on_model,
    room_brightness,
    threshold_from_reference,
    triangulate,
)
from app.mapper.detect_math import CameraIntrinsics, CameraPose
from app.mapper.schema_check import require_valid, validate

ROOT = Path(__file__).resolve().parents[1]


def test_binary_plan_is_log_plus_reference_frames():
    eight = code_frames(list(range(8)), "binary")
    assert len(eight) == bit_width(8) + 2 == 5
    assert eight[0]["role"] == "off" and eight[0]["lit"] == []
    assert eight[1]["role"] == "on" and eight[1]["lit"] == list(range(8))
    assert eight[2]["lit"] == [1, 3, 5, 7]
    assert eight[4]["lit"] == [4, 5, 6, 7]
    assert len(code_frames(list(range(500)), "binary")) == 11
    assert len(code_frames([4], "binary")) == 2
    singles = code_frames([10, 11, 12], "sequential")
    assert [frame["role"] for frame in singles] == ["off", "on", "single", "single", "single"]
    assert singles[2]["lit"] == [10]
    assert ddp_index(0, 60) == 60
    assert ddp_index(3, 0) == 3


def test_blobs_darkness_and_decode():
    image = [0] * (32 * 24)
    for dy in range(3):
        for dx in range(3):
            image[(6 + dy) * 32 + (8 + dx)] = 255
            image[(14 + dy) * 32 + (20 + dx)] = 220
    blobs = detect_blobs(image, 32, 24, 40)
    assert len(blobs) == 2
    assert blobs[0].x == pytest.approx(9, abs=0.2)
    assert blobs[0].y == pytest.approx(7, abs=0.2)
    reduced, width, _height = downsample_max(image, 32, 24, 2)
    found = detect_blobs(reduced, width, _height, 40)
    lifted = full_resolution(ImagePoint(found[0].x, found[0].y), 2)
    assert lifted.x == pytest.approx(blobs[0].x, abs=1.5)
    assert room_brightness([8] * 50).dark is True
    assert room_brightness([5] * 88 + [250] * 12).dark is False
    assert threshold_from_reference([4] * 8, [4, 4, 200]) == 102

    indices = [10, 11, 12, 13, 14]
    frames = code_frames(indices, "binary")
    spots = [ImagePoint(i * 20 + 5, 12) for i in range(5)]
    observations = []
    for frame in frames:
        if frame["role"] == "off":
            observations.append([ImagePoint(90, 40)])
        elif frame["role"] == "on":
            observations.append(spots + [ImagePoint(90, 40)])
        else:
            observations.append([spots[indices.index(index)] for index in frame["lit"]])
    decoded = decode_blobs(frames, observations, 3)
    assert {blob.index for blob in decoded if blob.index is not None} == set(indices)
    assert all(blob.index is None or blob.x != 90 for blob in decoded)
    alone = code_frames([7], "binary")
    seen = decode_blobs(alone, [[], [ImagePoint(4, 4)]], 2)
    assert seen[0].index == 7


def test_ray_triangulation_segment_and_corrections():
    intrinsics = CameraIntrinsics(200, 200, 100, 80)
    pose = CameraPose(Vec3(1, 0, 0), Vec3(0, 1, 0), Vec3(0, 0, 1), Vec3(0.1, 0, 1))
    world = Vec3(0.1, 0.2, 0)
    relative = world - pose.origin
    u = intrinsics.cx + intrinsics.fx * relative.x / -relative.z
    v = intrinsics.cy + intrinsics.fy * relative.y / relative.z
    ray = ray_from_pixel(u, v, intrinsics, pose)
    along = (world - ray.origin).dot(ray.direction)
    closest = ray.origin + ray.direction * along
    assert distance_ok(closest, world)
    other = Ray(Vec3(0.1, 1, 1), (world - Vec3(0.1, 1, 1)).normalized())
    hit = triangulate(ray, other)
    assert hit is not None
    assert hit.gap == pytest.approx(0, abs=1e-6)
    assert distance_ok(hit.position, world)

    model = [(0, Vec3(0, 0, 0), "span"), (1, Vec3(1, 0, 0), "span")]
    seen = assess_pixels(model, [[(0, Ray(Vec3(0, 0.01, 1), Vec3(0, 0, -1)))]], finished=False)
    assert seen[0].method == "segment" and seen[0].outlier is False
    assert seen[1].occluded is True and seen[1].missing is False
    done = assess_pixels(model, [[(0, Ray(Vec3(0, 0.01, 1), Vec3(0, 0, -1)))]], finished=True)
    assert done[1].missing is True and done[1].occluded is False

    above = Vec3(0, 1, 0)
    pair = [
        (0, Ray(Vec3(-1, 1, 1), (above - Vec3(-1, 1, 1)).normalized())),
        (0, Ray(Vec3(1, 1, 1), (above - Vec3(1, 1, 1)).normalized())),
    ]
    odd = assess_pixels(model, [pair], finished=True)
    assert odd[0].method == "triangulated" and odd[0].outlier is True
    assert odd[0].position.y == pytest.approx(1, abs=1e-6)

    refined = refine_on_model(
        Ray(Vec3(0.1, 0.02, 1), Vec3(0, 0, -1)),
        [("span", Vec3(0, 0, 0)), ("span", Vec3(0.2, 0, 0))],
        0.25,
    )
    assert refined is not None
    assert refined.position.x == pytest.approx(0.1, abs=1e-6)
    assert refined.position.y == pytest.approx(0, abs=1e-6)

    fixture = json.loads((ROOT / "fixtures" / "straight-1m.json").read_text(encoding="utf-8"))
    require_valid(fixture)
    fixture["schemaVersion"] = 2
    fixture["pixelCorrections"] = [
        {
            "id": "fix-1",
            "runId": "run-straight",
            "index": 0,
            "position": [0, 2, 0],
            "confidence": 0.8,
            "method": "segment",
            "viewpoints": 1,
        }
    ]
    fixture["waypoints"].append(
        {
            "id": "detected-tip",
            "position": [0, 1.2, 0],
            "source": "detected",
            "confidence": 0.7,
            "label": None,
        }
    )
    require_valid(fixture)
    pixels = compile_fixture(fixture)
    assert pixels[0].y == pytest.approx(2)
    assert pixels[1].y != pytest.approx(2)
    broken = dict(fixture)
    broken["schemaVersion"] = 3
    assert any("schemaVersion" in error for error in validate(broken))


def distance_ok(left: Vec3, right: Vec3) -> bool:
    return math.dist((left.x, left.y, left.z), (right.x, right.y, right.z)) == pytest.approx(0, abs=1e-6)
