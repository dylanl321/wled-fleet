import asyncio
import json

import httpx
import websockets

from app.config import Settings
from app.db import Database
from app.fleet import Fleet
from app.live import Hub, parse_http_live, parse_ws_frame, slice_frame
from app.lumen import Lumen, clean_look, is_default_name, merge_payload
from mock.wled_mock import MockSpec, parse_outputs
from tests.support import start_mock, stop_mock

PORCH = "b2:c3:d4:e5:f6:07"
NEW = "d4:e5:f6:07:18:29"


def specs():
    return [
        MockSpec(
            mac="b2c3d4e5f607", name="Dig-Uno Porch", mdns="porch", arch="esp8266", release="ESP8266",
            version="0.14.4", leds=60, outputs=[("Porch rail", 40), ("Porch ceiling", 20)], reboot_seconds=0.2,
        ),
        MockSpec(mac="d4e5f6071829", name="WLED", mdns="wled-a3f2", arch="esp32", release="ESP32", version="16.0.1", leds=30, reboot_seconds=0.2),
    ]


def test_helpers():
    assert is_default_name("WLED") and is_default_name("wled-a3f2") and not is_default_name("Porch")
    look = clean_look({"fx": "Candle", "fallback": "Fire Flicker", "col": [[1, 2, 3], "FF0000"], "sx": 999})
    assert look == {"fx": "Candle", "fallback": "Fire Flicker", "col": [[1, 2, 3], [255, 0, 0], [0, 0, 0]], "sx": 255}
    merged = merge_payload({"tt": 0, "seg": [{"id": 0, "fx": 1}, {"id": 1, "on": False}]}, {"tt": 4, "seg": [{"id": 0, "fx": 2}]})
    assert merged == {"tt": 4, "seg": [{"id": 0, "fx": 2}, {"id": 1, "on": False}]}
    assert parse_outputs("Porch rail:40, Porch ceiling:20") == [("Porch rail", 40), ("Porch ceiling", 20)]


def test_live_frames_parse_and_slice():
    frame = parse_ws_frame(b"L\x01" + bytes([255, 0, 0, 0, 255, 0, 0, 0, 255]))
    assert frame == [[255, 0, 0], [0, 255, 0], [0, 0, 255]]
    colors, step = parse_http_live({"leds": ["FF0000", "00FF00"], "n": 2})
    assert colors == [[255, 0, 0], [0, 255, 0]] and step == 2
    # A segment is cut from the board frame; long runs average in linear light.
    assert slice_frame(frame, 1, 1, 3) == bytes([0, 255, 0, 0, 0, 255])
    long = [[255, 255, 255], [0, 0, 0]] * 200
    averaged = slice_frame(long, 1, 0, 400, points=100)
    assert len(averaged) == 300
    assert 180 < averaged[0] < 190  # linear-light average of white and black, not 127


def test_lights_zones_looks_and_find(tmp_path):
    servers = [start_mock(spec) for spec in specs()]

    async def scenario():
        hosts = ",".join(f"127.0.0.1:{port}" for _server, port in servers)
        settings = Settings(data_dir=tmp_path, scheduler_enabled=False, extra_hosts=hosts)
        db = Database(tmp_path / "fleet.db")
        try:
            async with httpx.AsyncClient() as http:
                fleet = Fleet(db, settings, http)
                lumen = Lumen(fleet)
                await fleet.scan_once(full=True)
                await lumen.refresh()

                # Named boards become Lights, one per segment. A default-named board waits for Find.
                home = lumen.home()
                names = [light["name"] for light in home["lights"]]
                assert names == ["Porch rail", "Porch ceiling"]
                assert home["attention"]["new"] == 1
                assert len(home["looks"]) >= 20
                rail, ceiling = home["lights"]
                device = db.get_device(PORCH)

                async def board() -> dict:
                    return await fleet.wled.get_json(device, "/json/state")

                async def settle() -> None:
                    for writer in lumen.writers.values():
                        if writer.task:
                            await writer.task

                # A Look resolves by name per board and touches only that segment.
                aurora = next(look for look in home["looks"] if look["name"] == "Aurora")
                result = await lumen.apply([{"light": rail["id"], "look_id": aurora["id"]}])
                assert result["missing"] == []
                await settle()
                state = await board()
                effects, palettes = await lumen.catalog(device)
                assert state["seg"][0]["fx"] == effects.index("Aurora")
                assert state["seg"][0]["pal"] == palettes.index("Aurora")
                assert state["seg"][1]["fx"] == 0

                # A missing effect falls back to the Look's stand-in and is reported.
                await lumen.apply([{"light": ceiling["id"], "look": {"fx": "Not On This Build", "fallback": "Breathe"}}])
                await settle()
                state = await board()
                assert state["seg"][1]["fx"] == effects.index("Breathe")

                # Power is per segment; the sibling keeps running.
                await lumen.apply([{"light": ceiling["id"], "on": False}])
                await settle()
                state = await board()
                assert state["on"] is True and state["seg"][1]["on"] is False and state["seg"][0]["on"] is True

                # Brightness above the board master raises the master and rescales the sibling.
                await lumen.apply([{"light": ceiling["id"], "on": True}])
                await lumen.apply([{"light": rail["id"], "bri": 255}])
                await settle()
                state = await board()
                assert state["bri"] == 255
                assert state["seg"][0]["bri"] == 255
                assert state["seg"][1]["bri"] == 128  # was 255 of 128, still looks the same

                # Turning one light on when the whole board was off keeps its sibling dark.
                await fleet.wled.post_state(device, {"on": False})
                await lumen.refresh_board(device)
                lumen.quiet_until.clear()
                await lumen.refresh_board(device)
                await lumen.apply([{"light": rail["id"], "on": True}])
                await settle()
                state = await board()
                assert state["on"] is True and state["seg"][0]["on"] is True and state["seg"][1]["on"] is False

                # Zones, layout and favorites live on the server.
                zone = lumen.create_zone("Porch", [rail["id"], ceiling["id"]])
                assert {light["zone_id"] for light in lumen.light_views()} == {zone["id"]}
                lumen.set_layout([zone["id"]], [{"id": ceiling["id"], "zone_id": None}, {"id": rail["id"], "zone_id": zone["id"]}])
                views = {light["id"]: light for light in lumen.light_views()}
                assert views[ceiling["id"]]["zone_id"] is None
                lumen.set_layout([], [{"id": ceiling["id"], "zone_id": None}, {"id": rail["id"], "zone_id": None}])
                assert lumen.zones() == []  # dragging the last light out deletes the zone
                favorite = lumen.save_favorite("Movie night", [{"light": rail["id"], "on": True, "bri": 40, "look": {"fx": "Solid", "col": [[255, 60, 10]]}}])
                await lumen.apply_favorite(favorite["id"])
                await settle()
                state = await board()
                assert state["seg"][0]["fx"] == 0 and state["seg"][0]["col"][0] == [255, 60, 10]
                saved = lumen.save_look("Porch warm", {"fx": "Candle", "col": [[255, 120, 30]]})
                assert lumen.looks()[0]["id"] == saved["id"]

                # Find: light a span, then put the board back exactly.
                fresh = db.get_device(NEW)
                before = await fleet.wled.get_json(fresh, "/json/state")
                await lumen.span(NEW, 0, 25, 30)
                lit = await fleet.wled.get_json(fresh, "/json/state")
                assert lit["seg"][0]["i"][-3:] == [25, 30, "FFB000"]
                await lumen.release(NEW)
                after = await fleet.wled.get_json(fresh, "/json/state")
                assert after["seg"][0]["i"] == [] and after["seg"][0]["frz"] is False
                assert after["seg"][0]["col"] == before["seg"][0]["col"]

                # Naming it in Find puts it on Home.
                lumen.setup_board(NEW, [{"seg": 0, "name": "Garage door", "zone_id": None}])
                assert "Garage door" in [light["name"] for light in lumen.light_views()]
                assert lumen.attention()["new"] == 0
        finally:
            db.close()

    try:
        asyncio.run(scenario())
    finally:
        for server, _port in servers:
            stop_mock(server)


def test_hub_reads_the_board_socket(tmp_path):
    servers = [start_mock(spec) for spec in specs()[:1]]

    async def scenario():
        port = servers[0][1]
        settings = Settings(data_dir=tmp_path, scheduler_enabled=False, extra_hosts=f"127.0.0.1:{port}")
        db = Database(tmp_path / "fleet.db")
        try:
            async with httpx.AsyncClient() as http:
                fleet = Fleet(db, settings, http)
                lumen = Lumen(fleet)
                hub = Hub(fleet, lumen)
                await fleet.scan_once(full=True)
                await lumen.refresh()
                hub.sync_links()
                for _ in range(40):
                    if PORCH in hub.frames:
                        break
                    await asyncio.sleep(0.05)
                frame, step, _at = hub.frames[PORCH]
                assert len(frame) == 60 and step == 1
                assert hub.links[PORCH].mode == "socket"
                rail = lumen.light_views()[0]
                assert len(hub.light_frame(rail)) == 40 * 3
                hub.stop()
        finally:
            db.close()

    try:
        asyncio.run(scenario())
    finally:
        for server, _port in servers:
            stop_mock(server)


def test_mock_socket_streams_binary_frames():
    server, port = start_mock(specs()[0])

    async def scenario():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as socket:
            hello = json.loads(await socket.recv())
            assert [segment["n"] for segment in hello["state"]["seg"]] == ["Porch rail", "Porch ceiling"]
            await socket.send(json.dumps({"lv": True}))
            frame = await socket.recv()
            while not isinstance(frame, bytes):
                frame = await socket.recv()
            assert frame[:2] == b"L\x01" and (len(frame) - 2) // 3 == 60

    try:
        asyncio.run(scenario())
    finally:
        stop_mock(server)
