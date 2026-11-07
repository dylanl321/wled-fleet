"""Domain rules for Lights, Looks, and segment writes."""

import json

from app.db import Database
from app.discovery import DeviceFacts
from app.domain import (
    COMMIT_MS,
    BatchWriter,
    LightRef,
    WriteSequencer,
    board_profile,
    build_span_state,
    build_targeted_state,
    downsample_linear,
    migration_plan,
    parse_live_binary,
    preview_interval,
    resolve_look,
    should_send,
    slice_frame,
    sync_plan,
    transition_units,
)
from app.library import STARTER_LOOKS
from app.lumen import Lumen


def _light(light_id: int, segment: int, start: int, stop: int) -> LightRef:
    return LightRef(id=light_id, mac="aa", segment_id=segment, start=start, stop=stop)


def test_starter_library_has_about_twenty_four_named_looks():
    assert len(STARTER_LOOKS) >= 24
    names = [look["name"] for look in STARTER_LOOKS]
    assert names[0] == "Solid"
    for wanted in ("Candle", "Ember", "Aurora", "Ocean", "Rain", "Halloween", "Christmas classic", "Patriotic"):
        assert wanted in names
    for look in STARTER_LOOKS:
        assert "bri" not in look
        assert look["effect"] and look["fallback"]


def test_look_resolves_by_name_and_falls_back_per_firmware():
    effects = ["Solid", "Blink", "Breathe", "Rainbow", "Chase"]
    palettes = ["Default", "Ocean"]
    candle = resolve_look(
        {"effect": "Candle", "fallback": "Chase", "palette": "Fire", "colors": [[255, 80, 0]], "sx": 10, "ix": 20},
        effects,
        palettes,
    )
    assert candle["fallback"] is True
    assert candle["effect"] == "Chase"
    assert candle["fx"] == effects.index("Chase")
    assert candle["palette"] == "Default"
    direct = resolve_look(
        {"effect": "Rainbow", "fallback": "Solid", "palette": "Ocean", "colors": [[1, 2, 3]], "sx": 4, "ix": 5},
        effects,
        palettes,
    )
    assert direct["fallback"] is False
    assert direct["fx"] == 3
    assert direct["pal"] == 1


def test_segment_writes_leave_master_alone_until_every_light_is_selected():
    porch = [_light(1, 0, 0, 80), _light(2, 1, 80, 120)]
    one = build_targeted_state(porch, {1}, on=False, bri=40, fx=4, col=[[9, 8, 7]], tt_ms=0)
    assert "on" not in one
    assert "bri" not in one
    assert one["tt"] == 0
    assert one["seg"] == [{"id": 0, "start": 0, "stop": 80, "on": False, "bri": 40, "fx": 4, "col": [[9, 8, 7]]}]
    both = build_targeted_state(porch, {1, 2}, on=True, bri=200, tt_ms=COMMIT_MS)
    assert both["on"] is True
    assert both["bri"] == 200
    assert transition_units(400) == 4
    assert both["tt"] == 4
    assert [segment["id"] for segment in both["seg"]] == [0, 1]
    assert "bri" not in both["seg"][0]
    single = build_targeted_state([_light(7, 0, 0, 30)], {7}, on=False, bri=0, tt_ms=500)
    assert single["on"] is False
    assert single["bri"] == 0
    assert "start" not in single["seg"][0]


def test_preview_coalescing_drops_stale_previews_after_a_commit():
    gate = WriteSequencer()
    first = gate.submit("preview", {"tt": 0, "seg": [{"id": 0, "fx": 1}]})
    assert first["send"]["seg"][0]["fx"] == 1
    second = gate.submit("preview", {"tt": 0, "seg": [{"id": 0, "fx": 2}]})
    assert second["send"] is None
    third = gate.submit("preview", {"tt": 0, "seg": [{"id": 0, "fx": 3}]})
    assert third["dropped"] == [second["seq"]]
    assert gate.accepts(first["seq"]) is False
    commit = gate.submit("commit", {"tt": 4, "seg": [{"id": 0, "fx": 8}]})
    assert commit["send"]["seg"][0]["fx"] == 8
    assert third["seq"] in commit["dropped"]
    assert gate.accepts(first["seq"]) is False
    assert gate.accepts(commit["seq"]) is True
    follow = gate.finished(commit["seq"])
    assert follow is None
    # A preview that was waiting is released only after the in-flight one finishes,
    # and never after a newer commit.
    fresh = WriteSequencer()
    sent = fresh.submit("preview", {"n": 1})
    queued = fresh.submit("preview", {"n": 2})
    assert queued["send"] is None
    nxt = fresh.finished(sent["seq"])
    assert nxt["send"] == {"n": 2}
    assert fresh.accepts(queued["seq"]) is True


def test_esp8266_batches_writes_and_slows_the_fanout():
    profile = board_profile("esp8266")
    assert profile.live_fps == 5
    assert profile.max_sockets == 2
    assert profile.write_gap == 0.2
    assert board_profile("esp32").live_fps == 10
    writer = BatchWriter(0.2)
    assert writer.push(0.0, 1, {"a": 1}) == [(1, {"a": 1})]
    assert writer.push(0.05, 2, {"a": 2}) == []
    assert writer.push(0.10, 3, {"a": 3}) == []
    assert writer.flush(0.20) == [(3, {"a": 3})]
    assert should_send(True, 1, 0, 10) is True
    assert should_send(True, 1, 0, 5) is False
    assert should_send(True, 2, 0, 5) is True
    assert should_send(False, 5, 0, 10) is False
    assert should_send(False, 10, 0, 10) is True
    assert preview_interval(3) == 0.15
    assert preview_interval(14) == 0.40


def test_live_frames_slice_and_downsample_in_linear_light():
    frame = parse_live_binary(b"L\x01" + bytes([255, 0, 0, 0, 0, 0, 0, 0, 255]))
    assert frame == [[255, 0, 0], [0, 0, 0], [0, 0, 255]]
    assert parse_live_binary(b"L\x02\x02\x01" + bytes([1, 2, 3, 4, 5, 6])) == [[1, 2, 3], [4, 5, 6]]
    assert parse_live_binary(b"nope") is None
    sliced = slice_frame(frame, 1, 3, 3)
    assert sliced == [[0, 0, 0], [0, 0, 255]]
    # A black pixel beside a bright one must not disappear into the bright one.
    averaged = downsample_linear([[0, 0, 0], [255, 255, 255]], 1)
    assert averaged[0][0] < 200
    long = [[index, 0, 0] for index in range(1000)]
    assert len(downsample_linear(long, 10)) == 10


def test_span_targets_one_output_and_only_then_addresses_the_board():
    rail = _light(1, 0, 0, 100)
    ceiling = _light(2, 1, 100, 140)
    span = build_span_state(rail, 25, 50, [ceiling], only=False)
    assert "on" not in span
    assert span["seg"][0]["i"] == [25, 50, "FFE7B0"]
    assert len(span["seg"]) == 1
    only = build_span_state(rail, 0, 25, [ceiling], only=True)
    assert only["on"] is True
    assert only["seg"][1]["id"] == 1
    assert only["seg"][1]["on"] is False


def test_migration_keeps_devices_and_backups_and_maps_groups(tmp_path):
    db = Database(tmp_path / "fleet.db")
    mac = "a1:b2:c3:d4:e5:f0"
    other = "b2:c3:d4:e5:f6:07"
    db.upsert_device(
        DeviceFacts(mac=mac, ip="127.0.0.1", port=80, name="Porch", arch="esp32", led_count=120, info={"ver": "0.15.0", "mac": "a1b2c3d4e5f0"})
    )
    db.upsert_device(
        DeviceFacts(mac=other, ip="127.0.0.1", port=81, name="Shelf", arch="esp8266", led_count=60, info={"ver": "0.14.4", "mac": "b2c3d4e5f607"})
    )
    forgotten = "c3:d4:e5:f6:07:18"
    db.upsert_device(DeviceFacts(mac=forgotten, ip="10.0.0.9", port=80, name="Old", info={"ver": "0.13.0", "mac": "c3d4e5f60718"}))
    db.set_flags(forgotten, forgotten=True)
    group = db.create_group("Front of house", [mac])
    db.insert_backup(
        {
            "mac": mac,
            "created_at": "2026-01-01T00:00:00Z",
            "content_hash": "abc",
            "cfg_sha": "cfg",
            "presets_sha": "pre",
            "info_sha": "info",
            "reason": "nightly",
            "version": "0.15.0",
            "name": "Porch",
            "led_count": 120,
            "byte_size": 10,
        }
    )
    before_devices = len(db.list_devices())
    before_backups = len(db.list_backups(mac))
    buses = {
        mac: [
            {"index": 0, "start": 0, "len": 80, "pin": 16},
            {"index": 1, "start": 80, "len": 40, "pin": 1},
        ]
    }
    plan = migration_plan(db.list_devices(), db.list_groups(), buses)
    assert [zone.name for zone in plan.zones] == ["Front of house"]
    porch = [light for light in plan.lights if light.mac == mac]
    assert [(light.name, light.start, light.stop, light.named, light.zone_key) for light in porch] == [
        ("Porch 1", 0, 80, True, f"group-{group['id']}"),
        ("Porch 2", 80, 120, True, f"group-{group['id']}"),
    ]
    assert all(light.mac != forgotten for light in plan.lights)
    shelf = [light for light in plan.lights if light.mac == other]
    assert shelf[0].name == "Shelf"
    assert shelf[0].zone_key is None
    assert shelf[0].named is True

    class _Fleet:
        def __init__(self):
            self.db = db
            self.settings = type("S", (), {"app_version": "1.0.0"})()
            self.backups = type("B", (), {})()

    lumen = Lumen(_Fleet())
    lumen.prepare()
    assert db.get_setting("lumen_migrated") == "1"
    assert len(db.list_devices()) == before_devices
    assert len(db.list_backups(mac)) == before_backups
    assert db.get_device(mac)["name"] == "Porch"
    assert json.loads(db.get_device(mac)["info_json"])["ver"] == "0.15.0"
    lights = db.list_lights()
    assert {light["name"] for light in lights} == {"Porch", "Shelf"}
    assert all(light["named"] for light in lights)
    assert all(light["mac"] != forgotten for light in lights)
    zones = db.list_zones()
    assert zones[0]["name"] == "Front of house"
    assert db.list_looks()[0]["name"] == "Solid"
    assert len(db.list_looks()) >= 24
    assert (tmp_path / "fleet.db.pre-lumen").exists()
    reopened = Database(tmp_path / "fleet.db")
    again = Lumen(_Fleet())
    again.db = reopened
    again.prepare()
    assert len(reopened.list_lights()) == len(lights)
    assert len(reopened.list_zones()) == 1
    assert len(reopened.list_looks()) == len(db.list_looks())


def test_sync_adds_outputs_without_dropping_a_named_light():
    existing = [
        {"id": 4, "output_index": 0, "name": "Porch rail", "named": 1, "zone_id": 2, "position": 0, "start": 0, "stop": 120}
    ]
    buses = [
        {"index": 0, "start": 0, "len": 80},
        {"index": 1, "start": 80, "len": 40},
    ]
    plan = sync_plan(existing, buses, "Porch", 120)
    assert plan["delete_ids"] == []
    assert plan["upserts"][0]["id"] == 4
    assert plan["upserts"][0]["name"] == "Porch rail"
    assert plan["upserts"][0]["stop"] == 80
    assert plan["upserts"][1]["named"] is False
    assert plan["upserts"][1]["name"] == "Porch 2"
    unnamed = [
        {"id": 1, "output_index": 0, "name": "wled", "named": 0, "zone_id": None, "position": 0, "start": 0, "stop": 10},
        {"id": 2, "output_index": 3, "name": "gone", "named": 0, "zone_id": None, "position": 1, "start": 0, "stop": 4},
    ]
    shrunk = sync_plan(unnamed, [{"index": 0, "start": 0, "len": 10}], "wled", 10)
    assert shrunk["delete_ids"] == [2]
