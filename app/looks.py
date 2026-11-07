"""Turn WLED state and live-LED payloads into something the control page can draw."""

from __future__ import annotations

from app.errors import FleetError


def _byte(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if 0 <= value <= 255:
        return value
    return None


def _color(value: object) -> list[int] | None:
    if not isinstance(value, list) or len(value) < 3:
        return None
    red, green, blue = (_byte(value[0]), _byte(value[1]), _byte(value[2]))
    if red is None or green is None or blue is None:
        return None
    return [red, green, blue]


def parse_live(payload: object, limit: int = 48) -> list[list[int]]:
    """Accept the JSON shapes WLED uses for /json/live.

    Newer builds return {"leds": [[r,g,b], ...]}. Others flatten RGB into one
    array, and a few return the bare list.
    """
    raw = payload
    if isinstance(payload, dict):
        raw = payload.get("leds")
    colors: list[list[int]] = []
    if isinstance(raw, list) and raw and isinstance(raw[0], list):
        for item in raw:
            color = _color(item)
            if color:
                colors.append(color)
    elif isinstance(raw, list):
        numbers = [item for item in raw if isinstance(item, int) and not isinstance(item, bool)]
        for index in range(0, len(numbers) - 2, 3):
            color = _color(numbers[index : index + 3])
            if color:
                colors.append(color)
    if len(colors) <= limit:
        return colors
    if limit <= 0:
        return []
    step = len(colors) / limit
    return [colors[min(len(colors) - 1, int(index * step))] for index in range(limit)]


def summarize_state(state: dict) -> dict:
    segments_in = state.get("seg") if isinstance(state.get("seg"), list) else []
    main = state.get("mainseg") if isinstance(state.get("mainseg"), int) else 0
    chosen: dict = {}
    segments: list[dict] = []
    for index, segment in enumerate(segments_in):
        if not isinstance(segment, dict):
            continue
        colors = []
        for color in segment.get("col") or []:
            parsed = _color(color)
            if parsed:
                colors.append(parsed)
        while len(colors) < 3:
            colors.append([0, 0, 0])
        row = {
            "id": segment.get("id") if isinstance(segment.get("id"), int) else index,
            "start": segment.get("start") if isinstance(segment.get("start"), int) else 0,
            "stop": segment.get("stop") if isinstance(segment.get("stop"), int) else 0,
            "on": segment.get("on") is not False,
            "fx": segment.get("fx") if isinstance(segment.get("fx"), int) else 0,
            "pal": segment.get("pal") if isinstance(segment.get("pal"), int) else 0,
            "sx": segment.get("sx") if isinstance(segment.get("sx"), int) else 128,
            "ix": segment.get("ix") if isinstance(segment.get("ix"), int) else 128,
            "col": colors[:3],
        }
        segments.append(row)
        if row["id"] == main or not chosen:
            chosen = row
    udpn = state.get("udpn") if isinstance(state.get("udpn"), dict) else {}
    return {
        "on": bool(state.get("on")),
        "bri": state.get("bri") if isinstance(state.get("bri"), int) else 0,
        "ps": state.get("ps") if isinstance(state.get("ps"), int) else -1,
        "fx": chosen.get("fx", 0),
        "pal": chosen.get("pal", 0),
        "sx": chosen.get("sx", 128),
        "ix": chosen.get("ix", 128),
        "col": chosen.get("col", [[0, 0, 0], [0, 0, 0], [0, 0, 0]]),
        "segments": segments,
        "send": bool(udpn.get("send")),
        "recv": bool(udpn.get("recv")),
    }


def _clean_colors(value: object) -> list[list[int]]:
    if not isinstance(value, list) or not value:
        raise FleetError("Choose at least one color.")
    colors = []
    for item in value[:3]:
        color = _color(item)
        if color is None:
            raise FleetError("Colors need red, green, and blue from 0 to 255.")
        colors.append(color)
    while len(colors) < 3:
        colors.append([0, 0, 0])
    return colors


def build_state_payload(look: dict, segment_ids: list[int] | None = None) -> dict:
    """Build a partial /json/state body. Omitted fields are left alone on the controller."""
    payload: dict = {}
    if look.get("on") is not None:
        payload["on"] = bool(look["on"])
    if look.get("bri") is not None:
        bri = look["bri"]
        if not isinstance(bri, int) or not 0 <= bri <= 255:
            raise FleetError("Brightness must be from 0 to 255.")
        payload["bri"] = bri
    if look.get("ps") is not None:
        if not isinstance(look["ps"], int):
            raise FleetError("Preset must be a number.")
        payload["ps"] = look["ps"]
    if look.get("tt") is not None and isinstance(look["tt"], int):
        payload["tt"] = max(0, min(255, look["tt"]))
    segment: dict = {}
    if look.get("col") is not None:
        segment["col"] = _clean_colors(look["col"])
    for key in ("fx", "pal", "sx", "ix"):
        if look.get(key) is None:
            continue
        number = look[key]
        if not isinstance(number, int) or number < 0 or number > 255:
            raise FleetError(f"{key} is out of range.")
        segment[key] = number
    if segment:
        ids = segment_ids if segment_ids else [None]
        payload["seg"] = [{**segment, **({"id": segment_id} if segment_id is not None else {})} for segment_id in ids]
    if isinstance(look.get("udpn"), dict):
        payload["udpn"] = {
            key: bool(look["udpn"][key])
            for key in ("send", "recv")
            if key in look["udpn"]
        }
    if not payload:
        raise FleetError("Nothing to send.")
    return payload


def sync_flags(macs: list[str]) -> dict[str, dict]:
    """One board sends UDP sync. The others listen. A single board does both."""
    flags: dict[str, dict] = {}
    if len(macs) <= 1:
        for mac in macs:
            flags[mac] = {"send": True, "recv": True}
        return flags
    for index, mac in enumerate(macs):
        flags[mac] = {"send": index == 0, "recv": index != 0}
    return flags
