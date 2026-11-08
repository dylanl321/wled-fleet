"""Ownership, xSchedule, xLights controller sync, wiring, and show import."""

from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.discovery import DeviceFacts
from app.main import create_app
from app.mapper.compile import compile_fixture
from app.mapper.export import xlights_polyline, xlights_xmodel
from app.mapper.importers import import_rgbeffects
from app.mapper.ownership import OwnerState, decide
from app.mapper.player import XScheduleClient, playlist_names, playing_name
from app.mapper.realtime import confirm, foreign_live, inferred_owner
from app.mapper.xlights_sync import sync_show
from tests.test_mapper_api import STRAIGHT, PacketGrabber, _app, _device

ROOT = Path(__file__).resolve().parents[1]

NETWORKS = """<?xml version="1.0" encoding="UTF-8"?>
<networks computer="HOUSE">
  <Controller Id="1" Name="Dig-Quad-V3" Description="" Type="Ethernet" Vendor="WLED" Model="QuinLED Dig-Quad" Variant="" ActiveState="Active" IP="10.0.0.11" Protocol="DDP">
    <network ChannelsPerPacket="1440" KeepChannelNumbers="0" ComPort="10.0.0.11" BaudRate="1" NetworkType="DDP" MaxChannels="1" Enabled="Yes"/>
  </Controller>
  <Controller Id="2" Name="Dig-Uno-V3" Description="" Type="Ethernet" Vendor="WLED" Model="QuinLED Dig-Uno" Variant="" ActiveState="Active" IP="10.0.0.12" Protocol="DDP">
    <network ChannelsPerPacket="1440" KeepChannelNumbers="0" ComPort="10.0.0.12" BaudRate="1" NetworkType="DDP" MaxChannels="1" Enabled="Yes"/>
  </Controller>
  <Controller Id="3" Name="WLED-Gledopto" Description="" Type="Ethernet" Vendor="WLED" Model="WLED" Variant="Generic ESP32" ActiveState="Active" IP="10.0.0.13" Protocol="DDP">
    <network ChannelsPerPacket="1440" KeepChannelNumbers="0" ComPort="10.0.0.13" BaudRate="1" NetworkType="DDP" MaxChannels="1" Enabled="Yes"/>
  </Controller>
  <Controller Id="4" Name="Eaves" Description="" Type="Ethernet" Vendor="WLED" Model="WLED" Variant="Generic ESP32" ActiveState="Active" IP="10.0.0.14" Protocol="DDP">
    <network ChannelsPerPacket="1440" KeepChannelNumbers="0" ComPort="10.0.0.14" BaudRate="1" NetworkType="DDP" MaxChannels="780" Enabled="Yes"/>
  </Controller>
  <Controller Id="5" Name="Front Door" Description="" Type="Ethernet" Vendor="WLED" Model="WLED" Variant="Generic ESP32" ActiveState="Active" IP="10.0.0.15" Protocol="DDP">
    <network ChannelsPerPacket="1440" KeepChannelNumbers="0" ComPort="10.0.0.15" BaudRate="1" NetworkType="DDP" MaxChannels="900" Enabled="Yes"/>
  </Controller>
</networks>
"""

EFFECTS = """<?xml version="1.0" encoding="UTF-8"?>
<xrgb>
  <models>
    <model name="Eaves Run" DisplayAs="Poly Line" Controller="Eaves" NodesPerString="260" NumPoints="2" PointData="0,0,0,1,0,0" Dir="L"/>
    <model name="Front Door" DisplayAs="Poly Line" Controller="Front Door" NodesPerString="300" NumPoints="2" PointData="0,0,0,1,0,0" Dir="L"/>
    <model name="Porch" DisplayAs="Poly Line" Controller="Dig-Uno-V3" NodesPerString="20" NumPoints="2" PointData="0,0,0,1,0,0" Dir="L"/>
    <model name="Garage" DisplayAs="Poly Line" Controller="Dig-Quad-V3" NodesPerString="40" NumPoints="2" PointData="0,0,0,1,0,0" Dir="L"/>
    <model name="Window" DisplayAs="Poly Line" Controller="WLED-Gledopto" NodesPerString="20" NumPoints="2" PointData="0,0,0,1,0,0" Dir="L"/>
    <model name="Path lights" DisplayAs="Poly Line" Controller="Eaves" NodesPerString="30" NumPoints="2" PointData="0,0,0,1,0,0" Dir="L"/>
    <model name="WLED-Gledopto" DisplayAs="Poly Line" Controller="" NodesPerString="10" NumPoints="2" PointData="0,0,0,1,0,0" Dir="L"/>
    <model name="Hedge" DisplayAs="Poly Line" Controller="" NodesPerString="50" NumPoints="2" PointData="0,0,0,1,0,0" Dir="L"/>
    <model name="Columns" DisplayAs="Poly Line" Controller="" NodesPerString="100" NumPoints="2" PointData="0,0,0,1,0,0" Dir="L"/>
    <model name="Bare north" DisplayAs="Poly Line" Controller="" NodesPerString="0" NumPoints="2" PointData="0,0,0,1,0,0" Dir="L"/>
    <model name="Bare south" DisplayAs="Poly Line" Controller="" NodesPerString="0" NumPoints="2" PointData="0,1,0,1,1,0" Dir="L"/>
    <model name="Odd shrub" DisplayAs="Poly Line" Controller="" NodesPerString="12" NumPoints="2" PointData="0,0,0,1,0,0" Dir="L"/>
  </models>
</xrgb>
"""


def _fleet() -> list[dict]:
    rows = [
        ("aa:bb:cc:dd:ee:11", "Dig-Quad-V3", "10.0.0.99", 100),
        ("aa:bb:cc:dd:ee:12", "Dig-Uno-V3", "10.0.0.12", 100),
        ("aa:bb:cc:dd:ee:13", "WLED-Gledopto", "10.0.0.13", 50),
        ("aa:bb:cc:dd:ee:14", "Eaves", "10.0.0.14", 260),
        ("aa:bb:cc:dd:ee:15", "Front Door", "10.0.0.15", 300),
        ("aa:bb:cc:dd:ee:16", "Shed", "10.0.0.16", 40),
    ]
    return [
        {
            "id": mac,
            "name": name,
            "ip": ip,
            "outputs": [{"index": 0, "start": 0, "pixelCount": pixels, "pin": 16, "colorOrder": "GRB"}],
        }
        for mac, name, ip, pixels in rows
    ]


def _show(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "xlights_networks.xml").write_text(NETWORKS, encoding="utf-8")
    (folder / "xlights_rgbeffects.xml").write_text(EFFECTS, encoding="utf-8")


def _channels(name: str, root: ET.Element) -> str:
    for node in root.iter("Controller"):
        if node.attrib.get("Name") == name:
            return node.find("network").attrib["MaxChannels"]
    raise AssertionError(name)


class _Player:
    kind = "xschedule"

    def __init__(self) -> None:
        self.started: list[str] = []
        self.stopped: list[str] = []

    def configured(self) -> bool:
        return True

    async def playlists(self) -> list[str]:
        return ["Christmas"]

    async def start(self, playlist: str) -> dict:
        self.started.append(playlist)
        return {"result": "ok"}

    async def stop(self, playlist: str) -> dict:
        self.stopped.append(playlist)
        return {"result": "ok"}

    async def playing(self) -> str | None:
        return None


def test_ownership_transitions_snapshot_and_restore():
    fleet = OwnerState("fleet", "migration", None, None)
    to_show = decide(fleet, "xlights", "hook", "Christmas")
    assert to_show.snapshot is True
    assert to_show.restore is False
    assert to_show.state.owner == "xlights"
    assert to_show.state.playlist == "Christmas"
    holding = OwnerState("xlights", "hook", "Christmas", {"on": True, "bri": 40})
    again = decide(holding, "xlights", "hook", "Christmas")
    assert again.snapshot is False
    assert again.state.previous_state == {"on": True, "bri": 40}
    back = decide(holding, "fleet", "hook", None)
    assert back.restore is True
    assert back.state.previous_state is None
    assert back.state.owner == "fleet"
    parked = decide(holding, "none", "manual", None)
    assert parked.restore is False
    assert parked.state.previous_state == {"on": True, "bri": 40}
    resumed = decide(parked.state, "fleet", "manual", None)
    assert resumed.restore is True
    try:
        decide(fleet, "both", "manual", None)
    except ValueError as exc:
        assert "fleet" in str(exc)
    else:
        raise AssertionError("an unknown owner should be refused")


def test_realtime_inference_ignores_our_own_stream_and_a_held_show():
    local = {"10.0.0.2", "127.0.0.1"}
    assert foreign_live({"live": True, "lip": "10.9.9.9"}, local) is True
    assert foreign_live({"live": True, "lip": "10.0.0.2"}, local) is False
    assert foreign_live({"live": False, "lip": "10.9.9.9"}, local) is False
    fleet = OwnerState("fleet", "migration", None, None)
    assert inferred_owner({"live": True, "lip": "10.9.9.9"}, local, fleet) == "xlights"
    assert inferred_owner({"live": True, "lip": "10.0.0.2"}, local, fleet) is None
    inferred = OwnerState("xlights", "inferred", None, {"on": True})
    assert inferred_owner({"live": False, "lip": ""}, local, inferred) == "fleet"
    held = OwnerState("xlights", "hook", "Christmas", {"on": True})
    assert inferred_owner({"live": False, "lip": ""}, local, held) is None
    assert inferred_owner({"live": True, "lip": "10.9.9.9"}, local, held) is None
    streak, ready = confirm(0, "xlights")
    assert ready is None and streak == 1
    streak, ready = confirm(streak, "xlights")
    assert ready == "xlights" and streak == 0
    assert confirm(3, None) == (0, None)


def test_xschedule_client_plays_and_reads_status():
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.url.params.get("Command") or request.url.params.get("Query") or ""))
        if request.url.params.get("Query") == "GetPlayLists":
            return httpx.Response(200, json={"playlists": [{"Name": "Christmas", "id": 1}]})
        if request.url.params.get("Query") == "GetPlayingStatus":
            return httpx.Response(200, json={"status": "Playing", "PlayList": "Christmas"})
        if request.url.params.get("Command") == "Play specified playlist":
            assert request.url.params.get("Parameters") == "Christmas"
            return httpx.Response(200, json={"result": "ok"})
        if request.url.params.get("Command") == "Stop specified playlist":
            return httpx.Response(200, json={"result": "failed", "message": "nothing is playing"})
        return httpx.Response(404)

    client = XScheduleClient("http://xschedule.local", httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def scenario():
        assert await client.playlists() == ["Christmas"]
        assert await client.playing() == "Christmas"
        assert (await client.start("Christmas"))["result"] == "ok"
        try:
            await client.stop("Christmas")
        except Exception as exc:
            assert "nothing is playing" in str(exc)
        else:
            raise AssertionError("a failed xSchedule command should surface")

    asyncio.run(scenario())
    assert playlist_names([{"name": "One"}]) == ["One"]
    assert playing_name({"status": "Idle", "playlist": "Christmas"}) is None
    assert any(command == "Play specified playlist" for _path, command in seen)


def test_networks_diff_previews_then_applies_with_a_backup(tmp_path):
    folder = tmp_path / "show"
    _show(folder)
    preview = sync_show(folder, _fleet(), [], apply=False)
    assert preview["wrote"] is False
    assert preview["backups"] == []
    assert (folder / "xlights_networks.xml").read_text(encoding="utf-8") == NETWORKS
    placeholders = {item["name"] for item in preview["controllers"]["placeholders"]}
    assert placeholders == {"Dig-Quad-V3", "Dig-Uno-V3", "WLED-Gledopto"}
    assert preview["controllers"]["ipChanges"] == [{"name": "Dig-Quad-V3", "id": "aa:bb:cc:dd:ee:11", "from": "10.0.0.11", "to": "10.0.0.99"}]
    assert preview["controllers"]["missing"][0]["name"] == "Shed"
    assert set(preview["controllers"]["unchanged"]) == {"Eaves", "Front Door"}
    assigned = {item["model"]: item["controller"] for item in preview["models"]["assign"]}
    assert assigned == {"WLED-Gledopto": "WLED-Gledopto", "Hedge": "WLED-Gledopto"}
    assert preview["models"]["ambiguous"] == [{"model": "Columns", "candidates": ["Dig-Quad-V3", "Dig-Uno-V3"]}]
    assert preview["models"]["zeroPixels"] == ["Bare north", "Bare south"]
    assert {item["model"] for item in preview["models"]["unmatched"]} == {"Bare north", "Bare south", "Odd shrub"}
    assert len(preview["models"]["assigned"]) == 6

    applied = sync_show(folder, _fleet(), [], apply=True)
    assert applied["wrote"] is True
    assert len(applied["backups"]) == 2
    for backup in applied["backups"]:
        assert Path(backup).is_file()
        assert ".bak" in backup
    assert "MaxChannels=\"1\"" in Path(applied["backups"][0]).read_text(encoding="utf-8")
    root = ET.fromstring((folder / "xlights_networks.xml").read_text(encoding="utf-8"))
    assert _channels("Dig-Quad-V3", root) == "300"
    assert _channels("Dig-Uno-V3", root) == "300"
    assert _channels("WLED-Gledopto", root) == "150"
    assert _channels("Eaves", root) == "780"
    assert _channels("Front Door", root) == "900"
    quad = next(node for node in root.iter("Controller") if node.attrib.get("Name") == "Dig-Quad-V3")
    assert quad.attrib["IP"] == "10.0.0.99"
    assert quad.find("network").attrib["ComPort"] == "10.0.0.99"
    shed = next(node for node in root.iter("Controller") if node.attrib.get("Name") == "Shed")
    assert shed.attrib["Protocol"] == "DDP"
    assert shed.find("network").attrib["MaxChannels"] == "120"
    effects = ET.fromstring((folder / "xlights_rgbeffects.xml").read_text(encoding="utf-8"))
    by_name = {node.attrib["name"]: node.attrib.get("Controller") for node in effects.find("models")}
    assert by_name["Hedge"] == "WLED-Gledopto"
    assert by_name["WLED-Gledopto"] == "WLED-Gledopto"
    assert by_name["Columns"] == ""
    assert by_name["Bare north"] == ""
    assert by_name["Eaves Run"] == "Eaves"

    again = sync_show(folder, _fleet(), [], apply=True)
    assert again["wrote"] is False
    assert again["controllers"]["placeholders"] == []
    assert again["models"]["ambiguous"][0]["model"] == "Columns"


def test_export_wiring_block_uses_the_registry_output_start():
    fixture = json.loads(json.dumps(STRAIGHT))
    fixture["runs"][0]["outputIndex"] = 1
    controllers = [
        {
            "id": "aa:bb:cc:dd:ee:01",
            "name": "Dig-Uno",
            "ip": "10.0.0.8",
            "outputs": [
                {"index": 0, "start": 0, "pixelCount": 60, "colorOrder": "GRB"},
                {"index": 1, "start": 60, "pixelCount": 60, "colorOrder": "GRB"},
            ],
        }
    ]
    line = xlights_polyline(fixture["name"], fixture, controllers)
    wiring = ET.fromstring(line).find(".//wiring")
    assert wiring is not None
    assert wiring.attrib == {
        "controller": "Dig-Uno",
        "ip": "10.0.0.8",
        "protocol": "DDP",
        "output": "1",
        "startChannel": "181",
        "pixelCount": "60",
    }
    assert 'Controller="Dig-Uno"' in line
    assert 'StartChannel="181"' in line
    pixels = compile_fixture(fixture)
    model = xlights_xmodel("Straight 1m", pixels, surface_hint="freeform", width=10, height=6, controllers=controllers)
    model_wiring = ET.fromstring(model).find(".//wiring")
    assert model_wiring is not None
    assert model_wiring.attrib["protocol"] == "DDP"
    assert model_wiring.attrib["startChannel"] == "181"
    assert model_wiring.attrib["ip"] == "10.0.0.8"
    assert "<wiring " in xlights_polyline(fixture["name"], fixture)


def test_rgbeffects_import_scales_and_does_not_overwrite_a_phone_capture(tmp_path):
    show = """<?xml version="1.0" encoding="UTF-8"?>
<xrgb>
  <models>
    <model name="Hedge" DisplayAs="Poly Line" Controller="Dig-Uno" NodesPerString="60" NumPoints="2" PointData="0,0,0,10,0,0" WledPitch="100" Dir="L"/>
    <model name="Straight 1m" DisplayAs="Poly Line" Controller="Dig-Uno" NodesPerString="60" NumPoints="2" PointData="0,0,0,10,0,0" WledPitch="100" Dir="L"/>
    <model name="Bare north" DisplayAs="Poly Line" Controller="" NodesPerString="0" NumPoints="2" PointData="0,0,0,1,0,0" Dir="L"/>
  </models>
</xrgb>
"""
    parsed = import_rgbeffects(show, scale_metres_per_unit=0.1, filename="xlights_rgbeffects.xml")
    hedge = next(item["fixture"] for item in parsed if item["name"] == "Hedge")
    assert hedge["waypoints"][1]["position"][0] == 1.0
    assert any(item["skipped"] for item in parsed if item["name"] == "Bare north")
    app = _app(tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/mapper/fixtures", json=STRAIGHT).status_code == 200
        refused = client.post(
            "/api/mapper/fixtures/import",
            json={"source": show, "filename": "xlights_rgbeffects.xml", "scaleMetresPerUnit": 0.1},
        )
        assert refused.status_code == 200, refused.text
        body = refused.json()
        actions = {item["name"]: item["action"] for item in body["results"]}
        assert actions["Hedge"] == "created"
        assert actions["Straight 1m"] == "proposal"
        straight = next(item for item in body["results"] if item["name"] == "Straight 1m")
        assert straight["phoneCapture"] is True
        assert straight["changes"]["waypoints"]["from"] == 2
        kept = client.get(f"/api/mapper/fixtures/{STRAIGHT['id']}").json()
        assert kept["waypoints"][1]["position"] == [0, 1, 0]
        missing = client.post(
            "/api/mapper/import",
            json={"text": show, "filename": "xlights_rgbeffects.xml"},
        )
        assert missing.status_code == 400


def test_owner_blocks_the_renderer_and_comes_back_with_the_old_look(tmp_path):
    grabber = PacketGrabber()
    app = _app(tmp_path)
    posted: list[dict] = []

    async def state(_device, path, timeout=8.0):
        if path == "/json/state":
            return {"on": True, "bri": 40, "seg": [{"id": 0, "fx": 2}]}
        return {"live": False, "lip": "", "ver": "0.15.0", "mac": _device["mac"], "leds": {"count": 60}}

    async def post_state(_device, payload):
        posted.append(payload)
        return payload

    try:
        with TestClient(app) as client:
            app.state.mapper.ddp_port = grabber.port
            mac = "aa:bb:cc:dd:ee:01"
            _device(app, mac, leds=60)
            app.state.fleet.wled.get_json = state
            app.state.fleet.wled.post_state = post_state
            assert client.post("/api/mapper/fixtures", json=STRAIGHT).status_code == 200
            listed = client.get("/api/mapper/controllers").json()["controllers"]
            assert listed[0]["owner"] == "fleet"
            assert (tmp_path / "migration-backups" / "mapper-ownership.stamp").is_file()
            handed = client.post(f"/api/mapper/controllers/{mac}/owner", json={"owner": "xlights"})
            assert handed.status_code == 200, handed.text
            assert handed.json()["owner"] == "xlights"
            assert client.get("/api/mapper/status").json()["showRunning"] is True
            lit = client.post(
                f"/api/mapper/fixtures/{STRAIGHT['id']}/highlight",
                json={"controller": mac, "output": 0, "index": 0},
            )
            assert lit.status_code == 409
            assert client.post(
                f"/api/mapper/fixtures/{STRAIGHT['id']}/effect",
                json={"name": "fill-bottom", "params": {"seconds": 6}},
            ).status_code == 200
            assert grabber.collect(0.4) == []
            ledmap = client.post(
                f"/api/mapper/fixtures/{STRAIGHT['id']}/ledmap",
                json={"slot": 1, "mode": "height", "confirm": True},
            )
            assert ledmap.status_code == 409
            assert client.post(f"/api/devices/{mac}/reboot").status_code == 409
            restored = client.post(f"/api/mapper/controllers/{mac}/owner", json={"owner": "fleet"})
            assert restored.status_code == 200
            assert restored.json()["restored"] is True
            assert posted and posted[0]["bri"] == 40
            assert client.get("/api/mapper/controllers").json()["controllers"][0]["owner"] == "fleet"
    finally:
        grabber.close()


def test_show_hook_and_player_and_drift(tmp_path):
    folder = tmp_path / "show"
    _show(folder)
    app = create_app(Settings(data_dir=tmp_path, scheduler_enabled=False, player_url="", player_playlist=""))
    with TestClient(app) as client:
        boards = [
            ("aa:bb:cc:dd:ee:11", "Dig-Quad-V3", "10.0.0.11", 100),
            ("aa:bb:cc:dd:ee:12", "Dig-Uno-V3", "10.0.0.12", 100),
            ("aa:bb:cc:dd:ee:13", "WLED-Gledopto", "10.0.0.13", 50),
            ("aa:bb:cc:dd:ee:14", "Eaves", "10.0.0.14", 260),
            ("aa:bb:cc:dd:ee:15", "Front Door", "10.0.0.15", 300),
        ]
        for mac, name, ip, leds in boards:
            app.state.fleet.db.upsert_device(DeviceFacts(mac=mac, ip=ip, port=80, name=name, led_count=leds))
        hidden = client.get("/api/mapper/status").json()
        assert hidden["playerConfigured"] is False
        saved = client.put(
            "/api/mapper/settings",
            json={"playerUrl": "http://xschedule.local", "playerKind": "xschedule", "playlist": "Christmas", "xlightsShow": str(folder)},
        )
        assert saved.status_code == 200, saved.text
        assert client.get("/api/mapper/status").json()["playerConfigured"] is True
        player = _Player()
        app.state.mapper._player_override = player
        started = client.post("/api/mapper/shows/Christmas/start")
        assert started.status_code == 200, started.text
        assert player.started == ["Christmas"]
        assert client.get("/api/mapper/controllers").json()["controllers"][0]["owner"] == "xlights"
        stopped = client.post("/api/mapper/shows/Christmas/stop")
        assert stopped.status_code == 200, stopped.text
        assert player.stopped == ["Christmas"]
        assert client.get("/api/mapper/controllers").json()["controllers"][0]["owner"] == "fleet"
        hook = client.post("/api/mapper/hooks/xschedule", json={"event": "playlist start", "playlist": "Christmas"})
        assert hook.status_code == 200, hook.text
        assert client.get("/api/mapper/controllers").json()["controllers"][0]["owner"] == "xlights"
        ended = client.get("/api/mapper/hooks/xschedule", params={"event": "stop", "playlist": "Christmas"})
        assert ended.status_code == 200, ended.text
        assert client.get("/api/mapper/controllers").json()["controllers"][0]["owner"] == "fleet"
        drift = app.state.mapper.drift_check()
        assert drift["inSync"] is False
        assert drift["placeholders"] == 3
        assert client.get("/api/status").json()["xlightsDrift"]["placeholders"] == 3
        assert "MaxChannels=\"1\"" in (folder / "xlights_networks.xml").read_text(encoding="utf-8")
