import asyncio

import httpx

from app.config import Settings
from app.control import cached_view, remember_view, restore_sections, start_sections
from app.db import Database
from app.discovery import parse_info
from app.fleet import Fleet
from app.sections import build_section_state, buses_from_cfg, restore_body
from mock.wled_mock import MockSpec
from tests.support import start_mock, stop_mock


def test_buses_and_section_markers():
    buses = buses_from_cfg(
        {
            "hw": {
                "led": {
                    "ins": [
                        {"len": 20, "pin": [16]},
                        {"start": 20, "len": 10, "pin": [2]},
                        {"len": 0, "pin": [4]},
                    ]
                }
            }
        }
    )
    assert [bus["start"] for bus in buses] == [0, 20]
    assert buses[0]["pin"] == 16
    payload, legend = build_section_state(buses, 10, {"seg": [{"id": 0, "col": [[1, 2, 3]]}, {"id": 1, "on": True}]})
    assert payload["on"] is True and payload["bri"] == 255 and payload["tt"] == 0
    assert payload["seg"][0]["i"] == [0, 2, "FF2828", 10, 12, "FF2828", 20, 22, "28C85A"]
    assert payload["seg"][0]["frz"] is True
    assert payload["seg"][1] == {"id": 1, "on": False, "frz": False}
    assert legend[0]["output"] == 1 and legend[0]["pin"] == 16 and legend[0]["leds"] == 20
    assert legend[1]["color"] == [40, 200, 90]
    restored = restore_body({"on": True, "bri": 40, "seg": [{"id": 0, "fx": 5, "i": [1, 2, "FFFFFF"], "frz": True}]})
    assert restored["bri"] == 40
    assert restored["seg"][0]["fx"] == 5
    assert restored["seg"][0]["i"] == []
    assert restored["seg"][0]["frz"] is False


def test_section_size_is_rejected_when_it_floods_a_small_controller():
    buses = [{"index": 0, "start": 0, "len": 400, "pin": 16}]
    try:
        build_section_state(buses, 1)
    except ValueError as exc:
        assert "larger size" in str(exc)
    else:
        raise AssertionError("expected a size error")


def test_cached_control_does_not_ask_the_controllers(tmp_path):
    db = Database(tmp_path / "fleet.db")
    try:
        facts = parse_info(
            {"ver": "16.0.1", "mac": "a1b2c3d4e5f0", "name": "Shelf", "arch": "esp32", "leds": {"count": 30}},
            "127.0.0.1",
            9091,
        )
        db.upsert_device(facts)

        async def scenario():
            async with httpx.AsyncClient() as http:
                fleet = Fleet(db, Settings(data_dir=tmp_path, scheduler_enabled=False), http)
                view = cached_view(fleet, None)
                assert [board["name"] for board in view["devices"]] == ["Shelf"]
                assert view["devices"][0]["leds"] == []
                remember_view(
                    fleet,
                    {
                        "effects": ["Solid"],
                        "palettes": ["Default"],
                        "presets": [],
                        "devices": [{"mac": facts.mac, "name": "Old", "leds": [[9, 9, 9]], "effect": "Solid", "error": None, "presets": []}],
                    },
                )
                again = cached_view(fleet, None)
                assert again["devices"][0]["name"] == "Shelf"
                assert again["devices"][0]["leds"] == [[9, 9, 9]]
                assert again["effects"] == ["Solid"]

        asyncio.run(scenario())
    finally:
        db.close()


def test_flash_and_sections_restore_the_previous_look(tmp_path):
    spec = MockSpec(mac="a1b2c3d4e5f0", name="Shelf", mdns="shelf", arch="esp32", release="Dig-Uno-V3", version="16.0.1", leds=30, reboot_seconds=0.05)
    server, port = start_mock(spec)

    async def scenario():
        settings = Settings(data_dir=tmp_path, scheduler_enabled=False, extra_hosts=f"127.0.0.1:{port}")
        db = Database(tmp_path / "fleet.db")
        try:
            async with httpx.AsyncClient() as http:
                fleet = Fleet(db, settings, http)
                await fleet.scan_once(full=True)
                mac = "a1:b2:c3:d4:e5:f0"
                device = db.get_device(mac)
                await fleet.wled.post_cfg(
                    device,
                    {
                        "hw": {
                            "led": {
                                "ins": [
                                    {"start": 0, "len": 20, "pin": [16]},
                                    {"start": 20, "len": 10, "pin": [2]},
                                ]
                            }
                        }
                    },
                )
                await fleet.wled.post_state(device, {"on": True, "bri": 80, "tt": 0, "seg": [{"id": 0, "fx": 0, "col": [[1, 2, 3]]}]})
                await fleet.identify(mac, seconds=0.05)
                after_flash = await fleet.wled.get_json(device, "/json/state")
                assert after_flash["bri"] == 80
                assert after_flash["seg"][0]["col"][0] == [1, 2, 3]

                started = await start_sections(fleet, [mac], 10, timeout=0.2)
                assert started["results"][0]["ok"] is True
                assert started["devices"][0]["outputs"][0]["pin"] == 16
                assert started["devices"][0]["outputs"][1]["color"] == [40, 200, 90]
                marked = await fleet.wled.get_json(device, "/json/state")
                assert marked["seg"][0]["i"] == [0, 2, "FF2828", 10, 12, "FF2828", 20, 22, "28C85A"]
                assert marked["bri"] == 255

                restored = await restore_sections(fleet, [mac])
                assert restored[0]["restored"] is True
                back = await fleet.wled.get_json(device, "/json/state")
                assert back["bri"] == 80
                assert back["seg"][0]["col"][0] == [1, 2, 3]
                assert back["seg"][0]["i"] == []

                await start_sections(fleet, [mac], 10, timeout=0.15)
                await asyncio.sleep(0.45)
                timed = await fleet.wled.get_json(device, "/json/state")
                assert timed["bri"] == 80
                assert timed["seg"][0]["col"][0] == [1, 2, 3]
                for hold in list(fleet._section_holds.values()):
                    hold["task"].cancel()
        finally:
            db.close()

    try:
        asyncio.run(scenario())
    finally:
        stop_mock(server)
