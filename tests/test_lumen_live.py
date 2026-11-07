"""The server is the one live client per board and writes segments, not masters."""

import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from mock.wled_mock import MockSpec
from tests.support import start_mock, stop_mock


def _wait(predicate, timeout: float = 6.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.05)
    raise AssertionError(f"timed out, last={last!r}")


def test_live_fanout_and_segment_writes(tmp_path):
    porch = MockSpec(
        mac="aa11bb22cc33",
        name="Dig-Uno Porch",
        mdns="porch",
        arch="esp32",
        release="Dig-Uno-V3",
        version="0.15.1",
        leds=120,
        buses=((0, 80, 16), (80, 40, 1)),
        reboot_seconds=0.2,
    )
    shelf = MockSpec(
        mac="bb22cc33dd44",
        name="Dig-Uno Shelf",
        mdns="shelf",
        arch="esp8266",
        release="ESP8266",
        version="0.14.4",
        leds=60,
        reboot_seconds=0.2,
    )
    porch_server, porch_port = start_mock(porch)
    shelf_server, shelf_port = start_mock(shelf)
    try:
        settings = Settings(
            data_dir=tmp_path,
            scheduler_enabled=False,
            extra_hosts=f"127.0.0.1:{porch_port},127.0.0.1:{shelf_port}",
        )
        app = create_app(settings)
        with TestClient(app) as client:
            home = _wait(lambda: _ready(client.get("/api/lumen/home").json()))
            pending = {light["name"]: light for light in home["pending"]}
            assert "Dig-Uno Porch 1" in pending
            assert "Dig-Uno Porch 2" in pending
            assert "Dig-Uno Shelf" in pending
            assert home["lights"] == []
            assert home["attention"]["icon"] == "dot"
            rail = client.post(
                f"/api/lumen/find/{pending['Dig-Uno Porch 1']['mac']}/claim",
                json={"output": 0, "name": "Porch rail", "zone_name": "Front of house"},
            )
            assert rail.status_code == 200
            ceiling = client.post(
                f"/api/lumen/find/{pending['Dig-Uno Porch 2']['mac']}/claim",
                json={"output": 1, "name": "Porch ceiling", "zone_id": rail.json()["zone_id"]},
            )
            assert ceiling.status_code == 200
            shelf_light = client.post(
                f"/api/lumen/find/{pending['Dig-Uno Shelf']['mac']}/claim",
                json={"output": 0, "name": "Shelf"},
            )
            assert shelf_light.status_code == 200
            named = client.get("/api/lumen/home").json()
            assert {light["name"] for light in named["lights"]} == {"Porch rail", "Porch ceiling", "Shelf"}
            assert named["zones"][0]["name"] == "Front of house"
            assert named["pending"] == []

            rail_id = rail.json()["id"]
            ceiling_id = ceiling.json()["id"]
            look = next(item for item in named["looks"] if item["name"] == "Candle")
            one = client.post(
                "/api/lumen/apply",
                json={"light_ids": [rail_id], "kind": "commit", "look_id": look["id"], "bri": 80, "tt_ms": 400},
            )
            assert one.status_code == 200
            body = _wait(lambda: _write_touching(porch_port, rail_id=0))
            assert "bri" not in body
            assert "on" not in body
            assert body["tt"] == 4
            assert body["seg"][0]["id"] == 0
            assert body["seg"][0]["bri"] == 80
            assert "start" in body["seg"][0]
            both = client.post(
                "/api/lumen/apply",
                json={"light_ids": [rail_id, ceiling_id], "kind": "commit", "look_id": look["id"], "bri": 200, "on": True, "tt_ms": 400},
            )
            assert both.status_code == 200
            both_body = _wait(lambda: _write_with_master(porch_port))
            assert both_body["bri"] == 200
            assert both_body["on"] is True

            span = client.post(
                f"/api/lumen/find/{rail.json()['mac']}/span",
                json={"output": 0, "start": 25, "stop": 50},
            )
            assert span.status_code == 200
            painted = _wait(lambda: _write_with_pixels(porch_port))
            assert painted["seg"][0]["i"][0] == 25
            assert "on" not in painted or painted["seg"][0]["id"] == 0

            # The fleet process, not the phone, holds the board socket.
            _wait(lambda: _clients(shelf_port) == 1)
            _wait(lambda: _clients(porch_port) == 1)

            ids = [light["id"] for light in client.get("/api/lumen/home").json()["lights"]]
            with client.websocket_connect("/api/lumen/ws") as socket:
                socket.send_json({"type": "visible", "ids": ids})
                frame = _wait_socket(socket)
            assert frame["type"] == "frames"
            assert frame["items"]
            assert frame["items"][0]["leds"]

            # ESP8266 allows two sockets. The server already holds one.
            import websockets

            async def extras():
                import asyncio

                first = await websockets.connect(f"ws://127.0.0.1:{shelf_port}/ws")
                await asyncio.sleep(0.15)
                mid = _clients(shelf_port)
                third = await websockets.connect(f"ws://127.0.0.1:{shelf_port}/ws")
                await asyncio.sleep(0.15)
                after = _clients(shelf_port)
                await first.close()
                try:
                    await third.close()
                except Exception:
                    pass
                return mid, after

            # The third socket is accepted then closed, so the count stays at two.
            import asyncio

            mid, after = asyncio.run(extras())
            assert mid == 2
            assert after == 2
            assert _clients(shelf_port) <= 2
    finally:
        stop_mock(porch_server)
        stop_mock(shelf_server)


def _ready(home: dict):
    if len(home.get("pending") or []) >= 3 and home.get("looks"):
        return home
    return None


def _clients(port: int) -> int:
    import httpx

    try:
        return int(httpx.get(f"http://127.0.0.1:{port}/debug/ws", timeout=1).json()["clients"])
    except httpx.HTTPError:
        return -1


def _writes(port: int) -> list[dict]:
    import httpx

    return httpx.get(f"http://127.0.0.1:{port}/debug/ws", timeout=1).json()["writes"]


def _write_touching(port: int, rail_id: int):
    for row in reversed(_writes(port)):
        body = row["body"]
        segments = body.get("seg") or []
        if segments and segments[0].get("id") == rail_id and "bri" in segments[0] and "i" not in segments[0]:
            return body
    return None


def _write_with_master(port: int):
    for row in reversed(_writes(port)):
        body = row["body"]
        if body.get("bri") == 200 and body.get("on") is True:
            return body
    return None


def _write_with_pixels(port: int):
    for row in reversed(_writes(port)):
        body = row["body"]
        segments = body.get("seg") or []
        if segments and segments[0].get("i"):
            return body
    return None


def _wait_socket(socket):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        message = socket.receive_json()
        if message.get("type") == "frames":
            return message
    return None
