"""Section markers for counting LEDs, one color per WLED output."""

from __future__ import annotations

OUTPUT_COLORS: list[list[int]] = [
    [255, 40, 40],
    [40, 200, 90],
    [70, 120, 255],
    [255, 170, 40],
    [190, 80, 255],
    [40, 220, 220],
    [255, 70, 160],
    [255, 255, 255],
]

MAX_MARKERS = 160
MARKER_LEDS = 2


def buses_from_cfg(cfg: object) -> list[dict]:
    """Read hw.led.ins. Missing start values continue from the previous output."""
    if not isinstance(cfg, dict):
        return []
    hardware = cfg.get("hw") if isinstance(cfg.get("hw"), dict) else {}
    led = hardware.get("led") if isinstance(hardware, dict) else {}
    if not isinstance(led, dict):
        led = {}
    raw = led.get("ins")
    buses: list[dict] = []
    cursor = 0
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            length = item.get("len")
            if isinstance(length, bool) or not isinstance(length, int) or length <= 0:
                continue
            start = item.get("start")
            if isinstance(start, bool) or not isinstance(start, int) or start < 0:
                start = cursor
            pin = item.get("pin")
            if isinstance(pin, list):
                pin = pin[0] if pin else None
            if isinstance(pin, bool) or not isinstance(pin, int):
                pin = None
            buses.append({"index": len(buses), "start": start, "len": length, "pin": pin})
            cursor = start + length
    if not buses:
        total = led.get("total") if isinstance(led, dict) else None
        if isinstance(total, int) and not isinstance(total, bool) and total > 0:
            buses.append({"index": 0, "start": 0, "len": total, "pin": None})
    return buses


def color_for_output(index: int) -> list[int]:
    return list(OUTPUT_COLORS[index % len(OUTPUT_COLORS)])


def _hex(color: list[int]) -> str:
    return "".join(f"{channel:02X}" for channel in color)


def build_section_state(buses: list[dict], every: int, saved: dict | None = None) -> tuple[dict, list[dict]]:
    """Light a short marker every `every` LEDs. Each output uses its own color.

    One segment covers the strip and the `i` array paints only the markers, so
    this stays small enough for the ESP8266 JSON buffer when the spacing is sane.
    """
    if isinstance(every, bool) or not isinstance(every, int) or not 1 <= every <= 1000:
        raise ValueError("Section size must be from 1 to 1000.")
    if not buses:
        raise ValueError("This controller has no LED outputs in its config.")
    legend: list[dict] = []
    pixels: list[object] = []
    markers = 0
    for bus in buses:
        color = color_for_output(int(bus["index"]))
        legend.append(
            {
                "output": int(bus["index"]) + 1,
                "pin": bus.get("pin"),
                "start": int(bus["start"]),
                "leds": int(bus["len"]),
                "color": color,
                "every": every,
            }
        )
        position = 0
        length = int(bus["len"])
        start = int(bus["start"])
        while position < length:
            markers += 1
            if markers > MAX_MARKERS:
                raise ValueError("That section size makes too many markers for a small controller. Use a larger size.")
            span = min(MARKER_LEDS, length - position, every)
            led = start + position
            pixels.extend([led, led + span, _hex(color)])
            position += every
    total = max(int(bus["start"]) + int(bus["len"]) for bus in buses)
    segment = {
        "id": 0,
        "start": 0,
        "stop": total,
        "on": True,
        "fx": 0,
        "sx": 0,
        "frz": True,
        "col": [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
        "i": pixels,
    }
    segments: list[dict] = [segment]
    raw_segments = saved.get("seg") if isinstance(saved, dict) and isinstance(saved.get("seg"), list) else []
    for index, item in enumerate(raw_segments):
        if not isinstance(item, dict):
            continue
        seg_id = item.get("id") if isinstance(item.get("id"), int) else index
        if seg_id == 0:
            continue
        segments.append({"id": seg_id, "on": False, "frz": False})
    return {"on": True, "bri": 255, "tt": 0, "seg": segments}, legend


def replay_state(saved: dict) -> dict:
    """Put back the state captured before a flash, including segment pixels."""
    body: dict = {"tt": 0}
    if not isinstance(saved, dict):
        return body
    for key in ("on", "bri"):
        if key in saved:
            body[key] = saved[key]
    if isinstance(saved.get("transition"), int) and not isinstance(saved.get("transition"), bool):
        body["transition"] = saved["transition"]
    if isinstance(saved.get("seg"), list):
        body["seg"] = saved["seg"]
    return body


def restore_body(saved: dict) -> dict:
    """Leave section mode. Clears frozen individual LEDs and resumes the old segments."""
    body = replay_state(saved)
    raw = body.get("seg")
    if not isinstance(raw, list):
        return body
    cleaned = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        segment = {key: value for key, value in item.items() if key != "i"}
        segment["id"] = item.get("id") if isinstance(item.get("id"), int) else index
        segment["frz"] = False
        segment["i"] = []
        cleaned.append(segment)
    body["seg"] = cleaned
    return body
