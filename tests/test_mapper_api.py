"""Fixture API, DDP highlight, effect streaming, and ledmap upload."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.backup import BackupStore
from app.config import Settings
from app.db import Database
from app.discovery import DeviceFacts
from app.main import create_app
from mock.wled_mock import MockSpec
from tests.support import start_mock, stop_mock

ROOT = Path(__file__).resolve().parents[1]
STRAIGHT = json.loads((ROOT / "fixtures" / "straight-1m.json").read_text(encoding="utf-8"))
TREE = json.loads((ROOT / "fixtures" / "branching-tree.json").read_text(encoding="utf-8"))
SPLIT = json.loads((ROOT / "fixtures" / "two-controllers.json").read_text(encoding="utf-8"))


class PacketGrabber:
    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.15)

    @property
    def port(self) -> int:
        return int(self.sock.getsockname()[1])

    def collect(self, seconds: float) -> list[bytes]:
        deadline = time.monotonic() + seconds
        packets: list[bytes] = []
        while time.monotonic() < deadline:
            try:
                data, _addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            packets.append(data)
        return packets

    def close(self) -> None:
        self.sock.close()


def _app(tmp_path, password: str = ""):
    return create_app(Settings(data_dir=tmp_path, admin_password=password, scheduler_enabled=False))


def _device(app, mac: str, port: int = 80, leds: int = 60) -> None:
    app.state.fleet.db.upsert_device(
        DeviceFacts(mac=mac, ip="127.0.0.1", port=port, name="Dig-Uno", arch="esp32", led_count=leds)
    )


def test_password_still_covers_mapper(tmp_path):
    app = _app(tmp_path, "secret")
    with TestClient(app) as client:
        assert client.get("/api/mapper/fixtures").status_code == 401
        assert client.post("/api/login", json={"password": "secret"}).status_code == 200
        assert client.get("/api/mapper/fixtures").json() == {"fixtures": []}


def test_fixture_crud_pixelmap_and_migration_backup(tmp_path):
    db = Database(tmp_path / "fleet.db")
    db.upsert_device(DeviceFacts(mac="aa:bb:cc:dd:ee:01", ip="10.0.0.8", port=80, name="Shelf", led_count=60))
    db.close()
    app = _app(tmp_path)
    with TestClient(app) as client:
        backup = tmp_path / "migration-backups" / "fleet-before-mapper.db"
        assert backup.is_file()
        assert (tmp_path / "migration-backups" / "mapper-v1.stamp").is_file()
        copied = Database(backup)
        assert copied.get_device("aa:bb:cc:dd:ee:01")["name"] == "Shelf"
        copied.close()

        created = client.post("/api/mapper/fixtures", json=STRAIGHT)
        assert created.status_code == 200
        body = created.json()
        assert body["id"] == STRAIGHT["id"]
        assert body["createdAt"]
        listing = client.get("/api/mapper/fixtures").json()["fixtures"]
        assert len(listing) == 1
        pixelmap = client.get(f"/api/mapper/fixtures/{STRAIGHT['id']}/pixelmap").json()
        assert len(pixelmap["pixels"]) == 60
        assert pixelmap["runs"][0]["placed"] == 60
        broken = dict(STRAIGHT)
        broken["surfaceHint"] = "nope"
        assert client.put(f"/api/mapper/fixtures/{STRAIGHT['id']}", json=broken).status_code == 400
        assert client.delete(f"/api/mapper/fixtures/{STRAIGHT['id']}").json()["ok"] is True
        assert client.get(f"/api/mapper/fixtures/{STRAIGHT['id']}").status_code == 404


def test_controllers_read_color_order_from_cfg(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        mac = "aa:bb:cc:dd:ee:10"
        _device(app, mac, leds=100)
        cfg = {
            "hw": {
                "led": {
                    "total": 100,
                    "ins": [
                        {"start": 0, "len": 60, "pin": [16], "order": 0},
                        {"start": 60, "len": 40, "pin": [2], "order": 1, "rgbw": True},
                    ],
                }
            }
        }
        BackupStore(app.state.fleet.db, tmp_path).save(
            mac,
            json.dumps(cfg).encode(),
            b"{}",
            {"ver": "0.15.0", "name": "Dig-Uno", "leds": {"count": 100}},
            "test",
            5,
        )
        controllers = client.get("/api/mapper/controllers").json()["controllers"]
        assert controllers[0]["id"] == mac
        assert controllers[0]["outputs"][0]["colorOrder"] == "GRB"
        assert controllers[0]["outputs"][0]["pixelCount"] == 60
        assert controllers[0]["outputs"][1]["colorOrder"] == "RGBW"
        assert controllers[0]["outputs"][1]["start"] == 60


def test_second_output_highlight_uses_the_hardware_start(tmp_path):
    grabber = PacketGrabber()
    app = _app(tmp_path)
    try:
        with TestClient(app) as client:
            app.state.mapper.ddp_port = grabber.port
            mac = "aa:bb:cc:dd:ee:01"
            _device(app, mac, leds=120)
            cfg = {
                "hw": {
                    "led": {
                        "total": 120,
                        "ins": [
                            {"start": 0, "len": 60, "pin": [16], "order": 0},
                            {"start": 60, "len": 60, "pin": [2], "order": 0},
                        ],
                    }
                }
            }
            BackupStore(app.state.fleet.db, tmp_path).save(
                mac, json.dumps(cfg).encode(), b"{}", {"ver": "0.15.0", "name": "Dig-Uno", "leds": {"count": 120}}, "test", 5
            )
            fixture = json.loads(json.dumps(STRAIGHT))
            fixture["runs"][0]["outputIndex"] = 1
            fixture["runs"][0]["startIndex"] = 0
            assert client.post("/api/mapper/fixtures", json=fixture).status_code == 200
            lit = client.post(
                f"/api/mapper/fixtures/{fixture['id']}/highlight",
                json={"controller": mac, "output": 1, "index": 0},
            )
            assert lit.status_code == 200
            packets = grabber.collect(0.6)
            assert packets
            body = packets[0][10:]
            assert body[0:3] == bytes([0, 0, 0])
            assert body[60 * 3 : 60 * 3 + 3] == bytes([255, 255, 255])
    finally:
        grabber.close()


def test_old_start_index_is_converted_and_backed_up(tmp_path):
    mac = "aa:bb:cc:dd:ee:01"
    first = _app(tmp_path)
    with TestClient(first) as client:
        _device(first, mac, leds=120)
        cfg = {
            "hw": {"led": {"total": 120, "ins": [{"start": 0, "len": 60, "order": 0}, {"start": 60, "len": 60, "order": 0}]}}
        }
        BackupStore(first.state.fleet.db, tmp_path).save(
            mac, json.dumps(cfg).encode(), b"{}", {"ver": "0.15.0", "name": "Dig-Uno", "leds": {"count": 120}}, "test", 5
        )
        fixture = json.loads(json.dumps(STRAIGHT))
        fixture["runs"][0]["outputIndex"] = 1
        fixture["runs"][0]["startIndex"] = 60
        assert client.put(f"/api/mapper/fixtures/{fixture['id']}", json=fixture).status_code == 200
    second = _app(tmp_path)
    with TestClient(second) as client:
        saved = client.get(f"/api/mapper/fixtures/{STRAIGHT['id']}").json()
        assert saved["runs"][0]["startIndex"] == 0
        backup = tmp_path / "migration-backups" / "fixtures-before-startindex" / f"{STRAIGHT['id']}.json"
        assert json.loads(backup.read_text())["runs"][0]["startIndex"] == 60
        assert client.get(f"/api/mapper/fixtures/{STRAIGHT['id']}").json()["runs"][0]["startIndex"] == 0


def test_highlight_and_effect_use_ddp(tmp_path):
    grabber = PacketGrabber()
    app = _app(tmp_path)
    try:
        with TestClient(app) as client:
            app.state.mapper.ddp_port = grabber.port
            _device(app, "aa:bb:cc:dd:ee:01", leds=60)
            assert client.post("/api/mapper/fixtures", json=STRAIGHT).status_code == 200
            lit = client.post(
                f"/api/mapper/fixtures/{STRAIGHT['id']}/highlight",
                json={"controller": "aa:bb:cc:dd:ee:01", "output": 0, "index": 3},
            )
            assert lit.status_code == 200
            packets = grabber.collect(0.6)
            assert packets
            header, body = packets[0][:10], packets[0][10:]
            assert header[0] & 0x41 == 0x41 or header[0] & 0x40 == 0x40
            assert header[2] == 0x0B
            assert header[3] == 1
            assert len(header) == 10
            assert body[9:12] == bytes([255, 255, 255])
            assert body[0:3] == bytes([0, 0, 0])

            started = client.post(
                f"/api/mapper/fixtures/{STRAIGHT['id']}/effect",
                json={"name": "fill-bottom", "params": {"seconds": 30}},
            )
            assert started.status_code == 200
            frames = grabber.collect(0.45)
            fills = [packet for packet in frames if packet[10:13] == bytes([255, 244, 230])]
            assert len(fills) >= 2
            assert fills[0][40:43] == bytes([0, 0, 0])
            status = client.get("/api/mapper/status").json()
            assert status["running"] is True
            assert status["effect"] == "fill-bottom"
            grabber.collect(0.05)
            assert client.post(f"/api/mapper/fixtures/{STRAIGHT['id']}/stop").status_code == 200
            stopped = grabber.collect(0.4)
            assert any(packet[10:13] == bytes([0, 0, 0]) and packet[40:43] == bytes([0, 0, 0]) for packet in stopped)
            assert client.get("/api/mapper/status").json()["running"] is False
    finally:
        grabber.close()


def test_detect_steps_on_request_and_restores_the_previous_state(tmp_path):
    spec = MockSpec(
        mac="aabbccddee01",
        name="Shelf",
        mdns="shelf",
        arch="esp32",
        release="Dig-Uno",
        version="0.15.1",
        leds=60,
    )
    server, port = start_mock(spec)
    grabber = PacketGrabber()
    app = _app(tmp_path)
    try:
        with TestClient(app) as client:
            app.state.mapper.ddp_port = grabber.port
            _device(app, "aa:bb:cc:dd:ee:01", port=port, leds=60)
            assert client.post("/api/mapper/fixtures", json=STRAIGHT).status_code == 200
            started = client.post(
                f"/api/mapper/fixtures/{STRAIGHT['id']}/detect",
                json={"runId": "run-straight", "mode": "binary"},
            )
            assert started.status_code == 200, started.text
            session = started.json()
            assert session["frameCount"] == 8
            assert session["frames"][0]["role"] == "off"
            assert session["frames"][1]["lit"] == list(range(60))
            assert session["frames"][2]["role"] == "bit"
            assert session["frames"][2]["lit"][0] == 1
            assert session["outputStart"] == 0
            _post_state(port, {"bri": 40})
            assert _get_state(port)["bri"] == 40

            off = client.post(
                f"/api/mapper/fixtures/{STRAIGHT['id']}/detect/{session['sessionId']}",
                json={"step": 0},
            )
            assert off.status_code == 200
            assert off.json()["shown"] is True
            dark = _payloads(grabber.collect(0.35))
            assert dark
            assert dark[0][:3] == bytes([0, 0, 0])
            assert dark[0][3:6] == bytes([0, 0, 0])

            lit = client.post(
                f"/api/mapper/fixtures/{STRAIGHT['id']}/detect/{session['sessionId']}",
                json={"step": 1},
            )
            assert lit.json()["role"] == "on"
            bright = _payloads(grabber.collect(0.35))
            assert any(packet[:3] == bytes([255, 255, 255]) and packet[177:180] == bytes([255, 255, 255]) for packet in bright)

            bit = client.post(
                f"/api/mapper/fixtures/{STRAIGHT['id']}/detect/{session['sessionId']}",
                json={"step": 2},
            )
            assert bit.json()["bit"] == 0
            coded = _payloads(grabber.collect(0.35))
            assert any(packet[:3] == bytes([0, 0, 0]) and packet[3:6] == bytes([255, 255, 255]) for packet in coded)

            assert client.post(
                f"/api/mapper/fixtures/{STRAIGHT['id']}/detect/{session['sessionId']}",
                json={"step": 99},
            ).status_code == 400
            finished = client.post(f"/api/mapper/fixtures/{STRAIGHT['id']}/detect/{session['sessionId']}/finish")
            assert finished.status_code == 200
            assert finished.json()["restored"] is True
            assert _get_state(port)["bri"] == 128
            assert client.get("/api/mapper/status").json()["detect"] is None
            assert client.post(
                f"/api/mapper/fixtures/{STRAIGHT['id']}/detect/{session['sessionId']}",
                json={"step": 0},
            ).status_code == 404
    finally:
        grabber.close()
        stop_mock(server)


def test_detect_adds_the_output_start_for_each_run(tmp_path):
    spec = MockSpec(
        mac="aabbccddee11",
        name="Quad",
        mdns="quad",
        arch="esp32",
        release="Dig-Quad",
        version="0.15.1",
        leds=100,
    )
    server, port = start_mock(spec)
    grabber = PacketGrabber()
    app = _app(tmp_path)
    try:
        with TestClient(app) as client:
            app.state.mapper.ddp_port = grabber.port
            mac = "aa:bb:cc:dd:ee:11"
            _device(app, mac, port=port, leds=100)
            cfg = {
                "hw": {
                    "led": {
                        "total": 100,
                        "ins": [
                            {"start": 0, "len": 60, "pin": [16], "order": 0},
                            {"start": 60, "len": 40, "pin": [2], "order": 0},
                        ],
                    }
                }
            }
            BackupStore(app.state.fleet.db, tmp_path).save(
                mac,
                json.dumps(cfg).encode(),
                b"{}",
                {"ver": "0.15.0", "name": "Quad", "leds": {"count": 100}},
                "test",
                5,
            )
            fixture = {
                "schemaVersion": 1,
                "id": "detect-output",
                "name": "Second output",
                "origin": {"position": [0, 0, 0], "orientation": [0, 0, 0, 1]},
                "surfaceHint": "freeform",
                "waypoints": [
                    {"id": "out-a", "position": [0, 0, 0], "source": "typed", "confidence": 1, "label": None},
                    {"id": "out-b", "position": [0, 1, 0], "source": "typed", "confidence": 1, "label": None},
                ],
                "runs": [
                    {
                        "id": "run-out",
                        "controllerId": mac,
                        "outputIndex": 1,
                        "startIndex": 0,
                        "pixelCount": 3,
                        "pitchMm": 1000 / 3,
                        "colorOrder": "GRB",
                        "segments": [
                            {"id": "seg-out", "kind": "straight", "waypointIds": ["out-a", "out-b"], "params": {}}
                        ],
                    }
                ],
            }
            assert client.post("/api/mapper/fixtures", json=fixture).status_code == 200
            started = client.post(
                "/api/mapper/fixtures/detect-output/detect",
                json={"runId": "run-out", "mode": "sequential"},
            )
            assert started.status_code == 200, started.text
            session = started.json()
            assert session["outputStart"] == 60
            assert session["frameCount"] == 5
            assert session["indices"] == [0, 1, 2]
            shown = client.post(
                f"/api/mapper/fixtures/detect-output/detect/{session['sessionId']}",
                json={"step": 2},
            )
            assert shown.json()["role"] == "single"
            assert shown.json()["lit"] == [0]
            packets = _payloads(grabber.collect(0.35))
            assert packets
            assert packets[0][0:3] == bytes([0, 0, 0])
            assert packets[0][180:189] == bytes([255, 255, 255, 0, 0, 0, 0, 0, 0])
            assert client.post(f"/api/mapper/fixtures/detect-output/detect/{session['sessionId']}/finish").json()["restored"] is True
    finally:
        grabber.close()
        stop_mock(server)


def _payloads(packets: list[bytes]) -> list[bytes]:
    return [packet[10:] for packet in packets if len(packet) > 10]


def _get_state(port: int) -> dict:
    import urllib.request

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/state", timeout=3) as response:
        body = json.loads(response.read().decode())
    return body


def _post_state(port: int, payload: dict) -> None:
    import urllib.request

    data = json.dumps(payload).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/json/state",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3):
        return


def test_ledmap_requires_confirm_then_reboots_and_reads_back(tmp_path):
    spec = MockSpec(
        mac="aabbccddee02",
        name="Wall",
        mdns="wall",
        arch="esp32",
        release="Dig-Uno-V3",
        version="0.15.1",
        leds=180,
        reboot_seconds=0.05,
    )
    server, port = start_mock(spec)
    app = _app(tmp_path)
    try:
        with TestClient(app) as client:
            _device(app, "aa:bb:cc:dd:ee:02", port=port, leds=180)
            wall = json.loads((ROOT / "fixtures" / "wall-outline.json").read_text(encoding="utf-8"))
            assert client.post("/api/mapper/fixtures", json=wall).status_code == 200
            refused = client.post(
                f"/api/mapper/fixtures/{wall['id']}/ledmap",
                json={"slot": 1, "mode": "height", "confirm": False},
            )
            assert refused.status_code == 400
            pushed = client.post(
                f"/api/mapper/fixtures/{wall['id']}/ledmap",
                json={"slot": 1, "mode": "height", "confirm": True},
            )
            assert pushed.status_code == 200, pushed.text
            assert pushed.json()["confirmed"] is True
            assert pushed.json()["info"]["version"] == "0.15.1"
            tree = client.post("/api/mapper/fixtures", json=SPLIT)
            assert tree.status_code == 200
            spanned = client.post(
                f"/api/mapper/fixtures/{SPLIT['id']}/ledmap",
                json={"slot": 1, "mode": "height", "confirm": True},
            )
            assert spanned.status_code == 400
    finally:
        stop_mock(server)


def test_exports_download(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/mapper/fixtures", json=STRAIGHT).status_code == 200
        csv_body = client.get(f"/api/mapper/fixtures/{STRAIGHT['id']}/export?format=csv")
        assert csv_body.headers["content-type"].startswith("text/csv")
        assert csv_body.text.splitlines()[0].startswith("fixture,controller")
        model = client.get(f"/api/mapper/fixtures/{STRAIGHT['id']}/export?format=xmodel&width=10&height=6")
        assert '<models type="exported">' in model.text
        assert 'DisplayAs="Custom"' in model.text
        assert 'PixelCount="60"' in model.text
        line = client.get(f"/api/mapper/fixtures/{STRAIGHT['id']}/export?format=polyline")
        assert 'DisplayAs="Poly Line"' in line.text
        blaze = client.get(f"/api/mapper/fixtures/{STRAIGHT['id']}/export?format=pixelblaze")
        assert json.loads(blaze.text)[30][1] == 0.5
        imported = client.post(
            "/api/mapper/import",
            json={"filename": "straight.csv", "name": "From CSV", "text": csv_body.text},
        )
        assert imported.status_code == 200, imported.text
        again = client.get(f"/api/mapper/fixtures/{imported.json()['id']}/pixelmap").json()["pixels"]
        original = client.get(f"/api/mapper/fixtures/{STRAIGHT['id']}/pixelmap").json()["pixels"]
        assert len(again) == len(original)
        assert abs(again[30]["y"] - original[30]["y"]) < 1e-5


def test_branch_chase_accepts_a_target_and_rejects_an_unknown_one(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/mapper/fixtures", json=TREE).status_code == 200
        started = client.post(
            f"/api/mapper/fixtures/{TREE['id']}/effect",
            json={"name": "branch-chase", "params": {"target": "branches", "heightM": 1.4, "branchU": 0.2}},
        )
        assert started.status_code == 200, started.text
        assert started.json()["effect"] == "branch-chase"
        missing = client.post(
            f"/api/mapper/fixtures/{TREE['id']}/effect",
            json={"name": "fill-bottom", "params": {"target": "nope"}},
        )
        assert missing.status_code == 400
        saved = client.get(f"/api/mapper/fixtures/{TREE['id']}").json()
        assert saved["schemaVersion"] == 1
        assert saved["groups"][0]["role"] == "trunk"
        assert len(saved["references"]) == 3
