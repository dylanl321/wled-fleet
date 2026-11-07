import asyncio

import httpx

from app.config import Settings
from app.control import apply_look, set_sync, snapshot
from app.db import Database
from app.fleet import Fleet
from app.looks import build_state_payload, parse_live, sync_flags
from mock.wled_mock import MockSpec, render_live
from tests.support import start_mock, stop_mock


def test_parse_live_accepts_wled_shapes():
    assert parse_live({"leds": [[1, 2, 3], [4, 5, 6]], "n": 2}) == [[1, 2, 3], [4, 5, 6]]
    assert parse_live({"leds": [9, 8, 7, 1, 2, 3]}) == [[9, 8, 7], [1, 2, 3]]
    assert parse_live([[255, 0, 0]]) == [[255, 0, 0]]
    sampled = parse_live({"leds": [[index, 0, 0] for index in range(100)]}, limit=10)
    assert len(sampled) == 10
    assert sampled[0] == [0, 0, 0]


def test_chase_preview_moves():
    state = {
        "on": True,
        "bri": 255,
        "seg": [{"col": [[255, 0, 0], [0, 0, 40], [0, 0, 0]], "fx": 5, "sx": 220}],
    }
    assert render_live(state, 12, 0.0) != render_live(state, 12, 1.4)
    solid = render_live({"on": False, "bri": 255, "seg": [{"col": [[255, 0, 0]], "fx": 0}]}, 4, 0)
    assert solid == [[0, 0, 0]] * 4


def test_sync_flags_pick_one_sender():
    assert sync_flags(["a"])["a"] == {"send": True, "recv": True}
    flags = sync_flags(["a", "b", "c"])
    assert flags["a"]["send"] is True and flags["a"]["recv"] is False
    assert flags["b"] == {"send": False, "recv": True}
    assert flags["c"]["send"] is False


def test_payload_can_be_colors_or_a_preset_only():
    body = build_state_payload({"on": True, "bri": 10, "col": [[1, 2, 3]], "fx": 4}, [0, 2])
    assert body["seg"] == [
        {"col": [[1, 2, 3], [0, 0, 0], [0, 0, 0]], "fx": 4, "id": 0},
        {"col": [[1, 2, 3], [0, 0, 0], [0, 0, 0]], "fx": 4, "id": 2},
    ]
    assert build_state_payload({"ps": 2}) == {"ps": 2}


def test_groups_scenes_and_parallel_apply(tmp_path):
    specs = [
        MockSpec(mac="a1b2c3d4e5f0", name="Shelf", mdns="shelf", arch="esp32", release="Dig-Uno-V3", version="16.0.1", leds=30, reboot_seconds=0.2),
        MockSpec(mac="b2c3d4e5f607", name="Porch", mdns="porch", arch="esp8266", release="ESP8266", version="0.14.4", leds=20, reboot_seconds=0.2),
        MockSpec(mac="c3d4e5f60718", name="Hall", mdns="hall", arch="esp32", release="Dig-Uno-V3", version="16.0.1", leds=24, reboot_seconds=0.2),
    ]
    servers = [start_mock(spec) for spec in specs]

    async def scenario():
        hosts = ",".join(f"127.0.0.1:{port}" for _server, port in servers)
        settings = Settings(data_dir=tmp_path, scheduler_enabled=False, extra_hosts=hosts)
        db = Database(tmp_path / "fleet.db")
        try:
            async with httpx.AsyncClient() as http:
                fleet = Fleet(db, settings, http)
                await fleet.scan_once(full=True)
                macs = [row["mac"] for row in db.list_devices()]
                assert len(macs) == 3
                shelf, porch, hall = "a1:b2:c3:d4:e5:f0", "b2:c3:d4:e5:f6:07", "c3:d4:e5:f6:07:18"
                downstairs = db.create_group("Downstairs")
                night = db.create_group("Night path")
                db.set_group_members(downstairs["id"], [shelf, porch])
                db.add_group_member(night["id"], porch)
                db.add_group_member(night["id"], hall)
                assert porch in db.get_group(downstairs["id"])["macs"]
                assert porch in db.get_group(night["id"])["macs"]

                view = await snapshot(fleet, downstairs["id"])
                assert [board["name"] for board in view["devices"]] == ["Shelf", "Porch"]
                assert "Solid" in view["effects"]
                assert view["devices"][0]["leds"]

                look = {
                    "on": True,
                    "bri": 210,
                    "col": [[0, 40, 255], [0, 0, 20], [0, 0, 0]],
                    "fx": 5,
                    "pal": 2,
                    "sx": 200,
                    "ix": 80,
                }
                results = await apply_look(fleet, [shelf, porch], look, sync=True)
                assert [row["ok"] for row in results] == [True, True]
                shelf_state = await fleet.wled.get_json(db.get_device(shelf), "/json/state")
                porch_state = await fleet.wled.get_json(db.get_device(porch), "/json/state")
                hall_state = await fleet.wled.get_json(db.get_device(hall), "/json/state")
                assert shelf_state["bri"] == 210
                assert porch_state["seg"][0]["fx"] == 5
                assert porch_state["seg"][0]["col"][0] == [0, 40, 255]
                assert shelf_state["udpn"]["send"] is True
                assert porch_state["udpn"]["recv"] is True and porch_state["udpn"]["send"] is False
                assert hall_state["bri"] == 128
                assert hall_state["seg"][0]["fx"] == 0

                scene = db.create_scene("Reading", {"on": True, "bri": 40, "ps": 3})
                scene_results = await apply_look(fleet, [hall], scene["look"], sync=False)
                assert scene_results[0]["ok"] is True
                hall_after = await fleet.wled.get_json(db.get_device(hall), "/json/state")
                assert hall_after["ps"] == 3
                assert hall_after["bri"] == 40

                linked = await set_sync(fleet, night["id"], True)
                assert linked["group"]["sync"] is True
                porch_again = await fleet.wled.get_json(db.get_device(porch), "/json/state")
                hall_sync = await fleet.wled.get_json(db.get_device(hall), "/json/state")
                assert porch_again["udpn"]["send"] is True
                assert hall_sync["udpn"]["recv"] is True and hall_sync["udpn"]["send"] is False

                db.remove_group_member(downstairs["id"], porch)
                assert db.get_group(downstairs["id"])["macs"] == [shelf]
                assert porch in db.get_group(night["id"])["macs"]
                db.rename_group(night["id"], "Night")
                assert db.get_group(night["id"])["name"] == "Night"
                db.delete_group(downstairs["id"])
                assert db.get_group(downstairs["id"]) is None
                assert db.get_group(night["id"])["macs"] == [porch, hall]
        finally:
            db.close()

    try:
        asyncio.run(scenario())
    finally:
        for server, _port in servers:
            stop_mock(server)
