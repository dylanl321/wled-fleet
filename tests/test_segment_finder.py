"""Segment finder: cut one output into several Lights, on the board and in the database."""

import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.domain import (
    Piece,
    build_marks_state,
    build_split_state,
    finder_marks,
    heal_state,
    plan_split,
    sync_plan,
)
from app.main import create_app
from mock.wled_mock import MockSpec, paint_individual
from tests.support import start_mock, stop_mock


def light(id, start, stop, segment_id, output=0, name="Shelf", named=True):
    return {"id": id, "mac": "m", "start": start, "stop": stop, "segment_id": segment_id, "output_index": output, "name": name, "named": named, "zone_id": 3, "position": 0}


def test_plan_split_reuses_the_output_light_and_takes_free_segment_ids():
    pieces, leftover = plan_split([light(1, 0, 60, 0)], 0, 60, [20, 40, 40, 0, 60, 99], taken_ids={0, 1}, max_segments=16)
    assert pieces == [Piece(0, 20, 0, 1), Piece(20, 40, 2, None), Piece(40, 60, 3, None)]
    assert leftover == []


def test_plan_split_merge_back_leaves_the_extra_lights():
    existing = [light(1, 0, 20, 0), light(7, 20, 40, 2), light(8, 40, 60, 3)]
    pieces, leftover = plan_split(existing, 0, 60, [], taken_ids={0, 2, 3}, max_segments=16)
    assert pieces == [Piece(0, 60, 0, 1)]
    assert [row["id"] for row in leftover] == [7, 8]


def test_plan_split_respects_the_boards_segment_limit():
    with pytest.raises(ValueError, match="allows 2 segments"):
        plan_split([light(1, 0, 60, 0)], 0, 60, [10, 20], taken_ids={0}, max_segments=2)


def test_split_state_copies_the_look_to_new_pieces_and_deletes_removed_ones():
    template = {"id": 0, "col": [[1, 2, 3]], "fx": 9, "bri": 200, "on": True, "n": "ignored"}
    body = build_split_state([Piece(0, 20, 0, 1), Piece(20, 60, 2, None)], [5], template)
    assert body["seg"][0] == {"id": 0, "start": 0, "stop": 20, "frz": False}
    assert body["seg"][1]["col"] == [[1, 2, 3]] and body["seg"][1]["fx"] == 9 and "n" not in body["seg"][1]
    assert body["seg"][2] == {"id": 5, "stop": 0}


def test_marks_are_segment_relative():
    # Output 2 starts at LED 80. LED 85 is index 5 of its segment.
    body = build_marks_state([{"id": 1, "start": 80, "stop": 120}], [(85, 90, "FFFFFF")])
    assert body["seg"][0]["i"] == [0, 40, "000000", 5, 10, "FFFFFF"]
    # A proposed piece that spans two current segments is painted on both.
    body = build_marks_state([{"id": 0, "start": 0, "stop": 20}, {"id": 2, "start": 20, "stop": 60}], [(10, 30, "00FF00")])
    assert body["seg"][0]["i"][-3:] == [10, 20, "00FF00"]
    assert body["seg"][1]["i"][-3:] == [0, 10, "00FF00"]


def test_finder_marks_show_each_piece_its_boundary_and_the_marker():
    marks = finder_marks(0, 60, [20], marker=33)
    assert (0, 1, "FF3B30") in marks and (20, 21, "34C759") in marks
    assert marks[-1] == (33, 34, "FFFFFF")


def test_sync_plan_keeps_a_split_output_and_follows_it():
    existing = [light(1, 0, 20, 0), light(7, 20, 60, 2)]
    plan = sync_plan(existing, [{"index": 0, "start": 0, "len": 60}], "Shelf", 60)
    assert [(row["id"], row["start"], row["stop"], row["segment_id"]) for row in plan["upserts"]] == [(1, 0, 20, 0), (7, 20, 60, 2)]
    # The output moved and grew: pieces shift with it and the last one reaches the new end.
    plan = sync_plan(existing, [{"index": 0, "start": 10, "len": 80}], "Shelf", 90)
    assert [(row["start"], row["stop"]) for row in plan["upserts"]] == [(10, 30), (30, 90)]
    # Shrunk past the cut: the split collapses into its first Light.
    plan = sync_plan(existing, [{"index": 0, "start": 0, "len": 15}], "Shelf", 15)
    assert [(row["id"], row["start"], row["stop"]) for row in plan["upserts"]] == [(1, 0, 15)]
    assert plan["delete_ids"] == [7]


def test_heal_only_acts_on_split_boards_missing_their_segments():
    lights = [light(1, 0, 20, 0), light(7, 20, 60, 2)]
    assert heal_state(lights, {"seg": [{"id": 0, "start": 0, "stop": 20}, {"id": 2, "start": 20, "stop": 60}]}) is None
    healed = heal_state(lights, {"seg": [{"id": 0, "start": 0, "stop": 60}]})
    assert healed["seg"] == [{"id": 0, "start": 0, "stop": 20, "on": True}, {"id": 2, "start": 20, "stop": 60, "on": True}]
    assert heal_state([light(1, 0, 60, 0)], {"seg": [{"id": 0, "start": 0, "stop": 10}]}) is None


def test_mock_individual_leds_are_relative_to_the_segment():
    pixels = paint_individual(None, [0, 4, "000000", 1, 3, "FF0000", 3, [0, 0, 255]], 4)
    assert pixels == [[0, 0, 0], [255, 0, 0], [255, 0, 0], [0, 0, 255]]


def test_old_databases_are_migrated_to_allow_several_lights_per_output(tmp_path):
    path = tmp_path / "fleet.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE lights (
          id INTEGER PRIMARY KEY AUTOINCREMENT, mac TEXT NOT NULL, segment_id INTEGER NOT NULL,
          output_index INTEGER NOT NULL, name TEXT NOT NULL, zone_id INTEGER,
          position INTEGER NOT NULL DEFAULT 0, named INTEGER NOT NULL DEFAULT 0,
          start INTEGER NOT NULL DEFAULT 0, stop INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, UNIQUE (mac, output_index)
        );
        INSERT INTO lights (id, mac, segment_id, output_index, name, named, start, stop, created_at)
        VALUES (4, 'm', 0, 0, 'Porch rail', 1, 0, 60, 'now');
        """
    )
    raw.commit()
    raw.close()
    db = Database(path)
    try:
        assert db.get_light(4)["name"] == "Porch rail"
        db.insert_light({"mac": "m", "segment_id": 2, "output_index": 0, "name": "Porch rail 2", "start": 30, "stop": 60})
        assert [row["segment_id"] for row in db.lights_for_mac("m")] == [0, 2]
    finally:
        db.close()


def _wait(predicate, timeout: float = 6.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.05)
    raise AssertionError(f"timed out, last={last!r}")


def _board(port: int) -> dict:
    return httpx.get(f"http://127.0.0.1:{port}/json/state", timeout=2).json()


def _bounds(port: int) -> list[tuple[int, int, int]] | None:
    try:
        return sorted((seg["id"], seg["start"], seg["stop"]) for seg in _board(port)["seg"])
    except (httpx.HTTPError, ValueError):
        return None  # the board is rebooting


def test_split_preview_save_heal_and_merge(tmp_path):
    shelf = MockSpec(
        mac="cc33dd44ee55",
        name="Dig-Uno Shelf",
        mdns="shelf",
        arch="esp32",
        release="Dig-Uno-V3",
        version="0.15.1",
        leds=60,
        reboot_seconds=0.2,
    )
    server, port = start_mock(shelf)
    try:
        app = create_app(Settings(data_dir=tmp_path, scheduler_enabled=False, extra_hosts=f"127.0.0.1:{port}"))
        with TestClient(app) as client:
            home = _wait(lambda: (lambda h: h if h.get("pending") else None)(client.get("/api/lumen/home").json()))
            mac = home["pending"][0]["mac"]
            first = home["pending"][0]["id"]
            client.post(f"/api/lumen/find/{mac}/claim", json={"output": 0, "name": "Shelf"}).raise_for_status()
            client.post(f"/api/lumen/find/{mac}/flash", json={})  # the board must be linked
            client.post(f"/api/lumen/find/{mac}/flash", json={})

            # Preview paints the proposed pieces and the marker without changing the layout.
            preview = client.post(f"/api/lumen/find/{mac}/split/preview", json={"output": 0, "cuts": [20, 40], "marker": 33})
            assert preview.json()["pieces"] == 3
            frozen = _wait(lambda: (lambda seg: seg if seg.get("frz") else None)(_board(port)["seg"][0]))
            assert (33, 34, "FFFFFF") in [tuple(frozen["i"][i : i + 3]) for i in range(0, len(frozen["i"]), 3)]
            assert _bounds(port) == [(0, 0, 60)]

            # Cancel puts the strip back exactly as it was.
            client.post(f"/api/lumen/find/{mac}/split/cancel", json={}).raise_for_status()
            _wait(lambda: not _board(port)["seg"][0].get("frz"))
            client.post(f"/api/lumen/find/{mac}/split/preview", json={"output": 0, "cuts": [20, 40], "marker": 33})
            _wait(lambda: _board(port)["seg"][0].get("frz"))

            saved = client.post(f"/api/lumen/find/{mac}/split", json={"output": 0, "cuts": [20, 40], "names": ["Shelf top", "Shelf middle", "Shelf bottom"]})
            assert saved.status_code == 200, saved.text
            names = {row["name"]: row for row in saved.json()["lights"]}
            assert {"Shelf top", "Shelf middle", "Shelf bottom"} <= set(names)
            assert names["Shelf top"]["id"] == first
            _wait(lambda: _bounds(port) == [(0, 0, 20), (1, 20, 40), (2, 40, 60)])
            assert all(not seg.get("frz") for seg in _board(port)["seg"])

            # A reboot without a boot preset loses the split; the server writes it back.
            httpx.post(f"http://127.0.0.1:{port}/json/state", json={"rb": True}, timeout=2)
            _wait(lambda: _bounds(port) == [(0, 0, 20), (1, 20, 40), (2, 40, 60)], timeout=15)

            merged = client.post(f"/api/lumen/find/{mac}/split", json={"output": 0, "cuts": []})
            assert [row["name"] for row in merged.json()["lights"] if row["mac"] == mac] == ["Shelf top"]
            _wait(lambda: _bounds(port) == [(0, 0, 60)])
    finally:
        stop_mock(server)
