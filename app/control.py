"""Read every board's current look and push one design to many of them."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from app.discovery import preset_entries
from app.errors import FleetError, WledError
from app.looks import build_state_payload, parse_live, summarize_state, sync_flags
from app.sections import build_section_state, buses_from_cfg, color_for_output, restore_body

log = logging.getLogger(__name__)

SECTION_TIMEOUT = 45.0

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
        "outputs": await _outputs(fleet, device) if error is None else [],
        "error": error,
    }


async def _outputs(fleet, device: dict) -> list[dict]:
    try:
        raw = await fleet.wled.get_cfg_bytes(device)
        cfg = json.loads(raw)
    except (WledError, ValueError, UnicodeError):
        return []
    painted = []
    for bus in buses_from_cfg(cfg):
        painted.append(
            {
                "output": bus["index"] + 1,
                "start": bus["start"],
                "len": bus["len"],
                "pin": bus["pin"],
                "color": color_for_output(bus["index"]),
            }
        )
    return painted


async def snapshot(fleet, group_id: int | None) -> dict:
    devices = _devices_for(fleet, group_id)
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
    async with fleet.lock(device["mac"]):
        fleet._section_holds.pop(device["mac"], None)
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
        _touch_cache(fleet, device["mac"], look)
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


def _devices_for(fleet, group_id: int | None) -> list[dict]:
    devices = [row for row in fleet.db.list_devices() if not row["forgotten"]]
    if group_id is None:
        return devices
    group = fleet.db.get_group(group_id)
    if group is None:
        raise FleetError("Unknown group.", 404)
    wanted = set(group["macs"])
    chosen = [row for row in devices if row["mac"] in wanted]
    chosen.sort(key=lambda row: group["macs"].index(row["mac"]) if row["mac"] in group["macs"] else 99)
    return chosen


def _placeholder(fleet, row: dict) -> dict:
    return {
        "mac": row["mac"],
        "name": row["name"],
        "ip": row["ip"],
        "port": row["port"],
        "online": bool(row["online"]),
        "on": False,
        "bri": 0,
        "ps": -1,
        "fx": 0,
        "effect": "",
        "pal": 0,
        "palette": "",
        "sx": 128,
        "ix": 128,
        "col": [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
        "segments": [],
        "send": False,
        "recv": False,
        "leds": [],
        "presets": [],
        "groups": fleet.db.groups_for_mac(row["mac"]),
        "outputs": [],
        "error": None,
    }


def cached_view(fleet, group_id: int | None) -> dict:
    """Device list from the last refresh, not a live poll of every controller."""
    devices = _devices_for(fleet, group_id)
    meta = fleet._control_meta
    boards = []
    for row in devices:
        cached = fleet._board_cache.get(row["mac"])
        if cached:
            board = dict(cached)
            board["name"] = row["name"]
            board["ip"] = row["ip"]
            board["port"] = row["port"]
            board["online"] = bool(row["online"]) and cached.get("error") is None
            board["groups"] = fleet.db.groups_for_mac(row["mac"])
        else:
            board = _placeholder(fleet, row)
        boards.append(board)
    presets = list(meta.get("presets") or [])
    if not presets:
        for board in boards:
            if board.get("presets"):
                presets = board["presets"]
                break
    return {
        "effects": list(meta.get("effects") or []),
        "palettes": list(meta.get("palettes") or []),
        "presets": presets,
        "groups": fleet.db.list_groups(),
        "scenes": [
            {"id": scene["id"], "name": scene["name"], "look": scene["look"]}
            for scene in fleet.db.list_scenes()
        ],
        "devices": boards,
    }


def remember_view(fleet, view: dict) -> None:
    fleet._control_meta = {
        "effects": view.get("effects") or [],
        "palettes": view.get("palettes") or [],
        "presets": view.get("presets") or [],
    }
    for board in view.get("devices") or []:
        if isinstance(board, dict) and board.get("mac"):
            fleet._board_cache[board["mac"]] = dict(board)


def schedule_control_refresh(fleet) -> None:
    task = fleet._control_task
    if task is not None and not task.done():
        fleet._control_again = True
        return
    fleet._control_again = False
    fleet._control_task = asyncio.create_task(_refresh_control(fleet))


async def _refresh_control(fleet) -> None:
    try:
        while True:
            fleet._control_again = False
            started = fleet._control_gen
            view = await snapshot(fleet, None)
            if fleet._control_gen != started:
                continue
            remember_view(fleet, view)
            if not fleet._control_again:
                return
    except Exception:
        log.exception("control refresh failed")


def _touch_cache(fleet, mac: str, look: dict) -> None:
    fleet._control_gen += 1
    cached = fleet._board_cache.get(mac)
    if not cached:
        return
    board = dict(cached)
    if look.get("on") is not None:
        board["on"] = bool(look["on"])
    if isinstance(look.get("bri"), int) and not isinstance(look.get("bri"), bool):
        board["bri"] = look["bri"]
    if isinstance(look.get("fx"), int) and not isinstance(look.get("fx"), bool):
        board["fx"] = look["fx"]
        effects = fleet._control_meta.get("effects") or []
        if 0 <= look["fx"] < len(effects):
            board["effect"] = effects[look["fx"]]
    if isinstance(look.get("col"), list) and look["col"] and isinstance(look["col"][0], list):
        board["col"] = look["col"]
        level = (int(board.get("bri") or 0) / 255) if board.get("on") else 0
        scaled = [int(channel * level) for channel in look["col"][0][:3]]
        count = max(12, len(board.get("leds") or []) or 16)
        board["leds"] = [scaled for _ in range(count)]
    if look.get("on") is False:
        board["leds"] = [[0, 0, 0] for _ in (board.get("leds") or [[0, 0, 0]])]
    fleet._board_cache[mac] = board


def _arm_section(fleet, mac: str, state: dict, timeout: float) -> None:
    fleet._section_seq += 1
    generation = fleet._section_seq

    async def expire() -> None:
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return
        try:
            async with fleet.lock(mac):
                hold = fleet._section_holds.get(mac)
                if not hold or hold.get("gen") != generation:
                    return
                fleet._section_holds.pop(mac, None)
                device = fleet._require(mac)
                await fleet.wled.post_state(device, restore_body(hold["state"]))
        except (FleetError, WledError):
            log.warning("section restore failed for %s", mac, exc_info=True)

    fleet._section_holds[mac] = {"state": state, "gen": generation, "task": asyncio.create_task(expire())}


async def _sections_one(fleet, mac: str, every: int, timeout: float) -> dict:
    device = fleet._require(mac)
    previous = fleet._section_holds.pop(mac, None)
    if previous:
        try:
            await fleet.wled.post_state(device, restore_body(previous["state"]))
        except WledError:
            pass
    try:
        state = await fleet.wled.get_json(device, "/json/state", timeout=2.5)
        if not isinstance(state, dict):
            state = {}
        raw = await fleet.wled.get_cfg_bytes(device)
        cfg = json.loads(raw)
        payload, legend = build_section_state(buses_from_cfg(cfg), every, state)
        await fleet.wled.post_state(device, payload)
    except (WledError, ValueError) as exc:
        return {"result": {"mac": mac, "name": device["name"], "ok": False, "error": str(exc)}, "legend": None}
    _arm_section(fleet, mac, state, timeout)
    return {
        "result": {"mac": mac, "name": device["name"], "ok": True},
        "legend": {"mac": mac, "name": device["name"], "outputs": legend},
    }


async def start_sections(fleet, macs: list[str], every: int, timeout: float = SECTION_TIMEOUT) -> dict:
    if not macs:
        raise FleetError("Choose at least one controller.")

    async def one(mac: str) -> dict:
        async with fleet.lock(mac):
            return await _sections_one(fleet, mac, every, timeout)

    rows = await asyncio.gather(*(one(mac) for mac in macs))
    fleet.event("info", "control.sections", f"Marked every {every} LEDs on {len(macs)} controllers")
    return {
        "results": [row["result"] for row in rows],
        "devices": [row["legend"] for row in rows if row.get("legend")],
        "timeout": timeout,
    }


async def restore_sections(fleet, macs: list[str]) -> list[dict]:
    async def one(mac: str) -> dict:
        async with fleet.lock(mac):
            hold = fleet._section_holds.pop(mac, None)
            device = fleet.db.get_device(mac)
            name = device["name"] if device else mac
            if not hold:
                return {"mac": mac, "name": name, "ok": True, "restored": False}
            try:
                await fleet.wled.post_state(fleet._require(mac), restore_body(hold["state"]))
            except WledError as exc:
                return {"mac": mac, "name": name, "ok": False, "error": str(exc), "restored": False}
            return {"mac": mac, "name": name, "ok": True, "restored": True}

    results = list(await asyncio.gather(*(one(mac) for mac in macs)))
    fleet.event("info", "control.sections", "Restored controllers after section identify")
    return results
