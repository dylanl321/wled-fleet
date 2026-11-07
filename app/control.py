"""Read every board's current look and push one design to many of them."""

from __future__ import annotations

import asyncio
import json
import time

from app.discovery import preset_entries
from app.errors import FleetError, WledError
from app.looks import build_state_payload, parse_live, summarize_state, sync_flags

CATALOG_TTL = 60.0


def _name_at(names: list[str], index: int, fallback: str) -> str:
    if 0 <= index < len(names):
        return names[index]
    return f"{fallback} {index}"


async def _catalog(fleet, device: dict) -> tuple[list[str], list[str]]:
    cached = getattr(fleet, "_catalog", None)
    now = time.monotonic()
    if cached and now - cached[0] < CATALOG_TTL:
        return cached[1], cached[2]
    effects: list[str] = []
    palettes: list[str] = []
    try:
        raw_effects = await fleet.wled.get_json_any(device, "/json/eff", timeout=2.5)
        if isinstance(raw_effects, list):
            effects = [item for item in raw_effects if isinstance(item, str)]
    except WledError:
        effects = []
    try:
        raw_palettes = await fleet.wled.get_json_any(device, "/json/pal", timeout=2.5)
        if isinstance(raw_palettes, list):
            palettes = [item for item in raw_palettes if isinstance(item, str)]
    except WledError:
        palettes = []
    fleet._catalog = (now, effects, palettes)
    return effects, palettes


async def _one_board(fleet, device: dict, effects: list[str], palettes: list[str]) -> dict:
    summary = {
        "on": False,
        "bri": 0,
        "ps": -1,
        "fx": 0,
        "pal": 0,
        "sx": 128,
        "ix": 128,
        "col": [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
        "segments": [],
        "send": False,
        "recv": False,
    }
    leds: list[list[int]] = []
    presets: list[dict] = []
    error = None
    try:
        state = await fleet.wled.get_json(device, "/json/state", timeout=2.5)
        summary = summarize_state(state)
    except WledError as exc:
        error = str(exc)
    if error is None:
        try:
            live = await fleet.wled.get_json_any(device, "/json/live", timeout=1.5)
            leds = parse_live(live)
        except WledError:
            leds = []
        try:
            raw_presets = await fleet.wled.get_presets_bytes(device)
            presets = preset_entries(json.loads(raw_presets))
        except (WledError, ValueError):
            presets = []
    if not leds and summary["col"]:
        color = [int(channel * summary["bri"] / 255) for channel in summary["col"][0]] if summary["on"] else [0, 0, 0]
        leds = [color for _ in range(16)]
    return {
        "mac": device["mac"],
        "name": device["name"],
        "ip": device["ip"],
        "port": device["port"],
        "online": error is None and bool(device["online"]),
        "on": summary["on"],
        "bri": summary["bri"],
        "ps": summary["ps"],
        "fx": summary["fx"],
        "effect": _name_at(effects, summary["fx"], "Effect"),
        "pal": summary["pal"],
        "palette": _name_at(palettes, summary["pal"], "Palette"),
        "sx": summary["sx"],
        "ix": summary["ix"],
        "col": summary["col"],
        "segments": summary["segments"],
        "send": summary["send"],
        "recv": summary["recv"],
        "leds": leds,
        "presets": presets,
        "groups": fleet.db.groups_for_mac(device["mac"]),
        "error": error,
    }


async def snapshot(fleet, group_id: int | None) -> dict:
    devices = [row for row in fleet.db.list_devices() if not row["forgotten"]]
    if group_id is not None:
        group = fleet.db.get_group(group_id)
        if group is None:
            raise FleetError("Unknown group.", 404)
        wanted = set(group["macs"])
        devices = [row for row in devices if row["mac"] in wanted]
        devices.sort(key=lambda row: group["macs"].index(row["mac"]) if row["mac"] in group["macs"] else 99)
    effects: list[str] = []
    palettes: list[str] = []
    source = next((row for row in devices if row["online"]), None)
    if source is not None:
        effects, palettes = await _catalog(fleet, source)
    boards = await asyncio.gather(*(_one_board(fleet, row, effects, palettes) for row in devices))
    presets: list[dict] = []
    for board in boards:
        if board["presets"]:
            presets = board["presets"]
            break
    return {
        "effects": effects,
        "palettes": palettes,
        "presets": presets,
        "groups": fleet.db.list_groups(),
        "scenes": [
            {"id": scene["id"], "name": scene["name"], "look": scene["look"]}
            for scene in fleet.db.list_scenes()
        ],
        "devices": list(boards),
    }


async def _push(fleet, device: dict, look: dict, udpn: dict | None) -> dict:
    segment_ids: list[int] | None = None
    try:
        state = await fleet.wled.get_json(device, "/json/state", timeout=2.5)
        summary = summarize_state(state)
        if summary["segments"] and any(key in look for key in ("col", "fx", "pal", "sx", "ix")):
            segment_ids = [segment["id"] for segment in summary["segments"]]
    except WledError:
        segment_ids = None
    body = dict(look)
    if udpn is not None:
        body["udpn"] = udpn
    payload = build_state_payload(body, segment_ids)
    await fleet.wled.post_state(device, payload)
    return {"mac": device["mac"], "name": device["name"], "ok": True}


async def apply_look(fleet, macs: list[str], look: dict, sync: bool) -> list[dict]:
    if not macs:
        raise FleetError("Choose at least one controller.")
    devices = [fleet._require(mac) for mac in macs]
    flags = sync_flags(macs) if sync else {}

    async def one(device: dict) -> dict:
        try:
            return await _push(fleet, device, look, flags.get(device["mac"]))
        except FleetError as exc:
            return {"mac": device["mac"], "name": device["name"], "ok": False, "error": str(exc)}

    results = await asyncio.gather(*(one(device) for device in devices))
    ok = sum(1 for result in results if result["ok"])
    fleet.event("info", "control.apply", f"Set a look on {ok} of {len(results)} controllers")
    return list(results)


async def set_sync(fleet, group_id: int, enabled: bool) -> dict:
    group = fleet.db.get_group(group_id)
    if group is None:
        raise FleetError("Unknown group.", 404)
    fleet.db.set_group_sync(group_id, enabled)
    macs = [mac for mac in group["macs"] if fleet.db.get_device(mac)]
    flags = sync_flags(macs) if enabled else {mac: {"send": False} for mac in macs}
    results = []
    for mac in macs:
        device = fleet._require(mac)
        try:
            await fleet.wled.post_state(device, {"udpn": flags[mac], "v": True})
            results.append({"mac": mac, "name": device["name"], "ok": True})
        except FleetError as exc:
            results.append({"mac": mac, "name": device["name"], "ok": False, "error": str(exc)})
    fleet.event(
        "info",
        "control.sync",
        f"{'Linked' if enabled else 'Unlinked'} effects in {group['name']}",
    )
    updated = fleet.db.get_group(group_id)
    assert updated is not None
    return {"group": updated, "results": results}
