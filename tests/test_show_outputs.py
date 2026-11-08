"""Show outputs: every controller output lit in its own color, then put back."""

import time

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain import OUTPUT_COLORS, build_outputs_state, output_marks
from app.main import create_app
from mock.wled_mock import MockSpec
from tests.support import start_mock, stop_mock


def test_marks_are_unique_and_neighbours_differ():
    marks = output_marks(30)
    keys = [(mark["color"], mark["pattern"]) for mark in marks]
    assert len(set(keys)) == 30
    # The first round is solid, then the colors come back dashed, then dotted.
    assert {mark["pattern"] for mark in marks[: len(OUTPUT_COLORS)]} == {"solid"}
    assert marks[len(OUTPUT_COLORS)]["pattern"] == "dashed"
    assert marks[2 * len(OUTPUT_COLORS)]["pattern"] == "dotted"
    # Consecutive outputs (usually on the same board) never get the same color.
    assert all(a["color"] != b["color"] for a, b in zip(marks, marks[1:]))


def test_outputs_state_uses_grouping_for_patterns():
    marks = output_marks(13)
    body = build_outputs_state([{"id": 0, "start": 0, "stop": 60, "mark": marks[12]}])
    segment = body["seg"][0]
    assert segment["grp"] == 3 and segment["spc"] == 2 and segment["fx"] == 0
    assert body["on"] is True and body["bri"] == 255


def _wait(predicate, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.05)
    raise AssertionError(f"timed out, last={last!r}")


def _state(port: int) -> dict:
    return httpx.get(f"http://127.0.0.1:{port}/json/state", timeout=2).json()


def test_show_outputs_lights_every_output_and_puts_them_back(tmp_path):
    quad = MockSpec(mac="a0c4f1000001", name="Eaves", mdns="eaves", arch="esp32", release="Dig-Quad-V3", version="0.15.1", leds=240, buses=((0, 60, 16), (60, 60, 17), (120, 60, 18), (180, 60, 19)), reboot_seconds=0.2)
    uno = MockSpec(mac="a0c4f1000002", name="Porch", mdns="porch", arch="esp8266", release="ESP8266", version="0.14.4", leds=60, reboot_seconds=0.2)
    servers = [start_mock(quad), start_mock(uno)]
    try:
        hosts = ",".join(f"127.0.0.1:{port}" for _server, port in servers)
        app = create_app(Settings(data_dir=tmp_path, scheduler_enabled=False, extra_hosts=hosts))
        with TestClient(app) as client:
            _wait(lambda: len(client.get("/api/lumen/home").json().get("pending") or []) >= 5)
            quad_port, uno_port = servers[0][1], servers[1][1]
            before = _state(quad_port)

            shown = client.post("/api/lumen/outputs/show").json()
            legend = shown["legend"]
            assert [(row["board"], row["output"]) for row in legend] == [("Eaves", 1), ("Eaves", 2), ("Eaves", 3), ("Eaves", 4), ("Porch", 1)]
            assert len({row["color"] for row in legend}) == 5

            def lit():
                state = _state(quad_port)
                colors = ["%02X%02X%02X" % tuple(segment["col"][0]) for segment in state["seg"]]
                return colors if colors == [row["color"] for row in legend[:4]] else None

            _wait(lit)
            # The ESP8266 board's writes are batched, so it can trail the ESP32 a little.
            want = [int(legend[4]["color"][i : i + 2], 16) for i in (0, 2, 4)]
            _wait(lambda: _state(uno_port)["seg"][0]["col"][0] == want)

            client.post("/api/lumen/outputs/hide").raise_for_status()
            _wait(lambda: [segment["col"][0] for segment in _state(quad_port)["seg"]] == [segment["col"][0] for segment in before["seg"]])
            assert all(segment.get("spc", 0) == 0 for segment in _state(quad_port)["seg"])
    finally:
        for server, _port in servers:
            stop_mock(server)
