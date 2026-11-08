"""Controller list for the mapper: one record per board, with outputs from cfg."""

from __future__ import annotations

import json

COLOR_ORDERS = ("GRB", "RGB", "BRG", "RBG", "BGR", "GBR")


def color_order_name(raw_order: object, rgbw: object) -> str:
    index = raw_order if isinstance(raw_order, int) and not isinstance(raw_order, bool) else 0
    name = COLOR_ORDERS[index] if 0 <= index < len(COLOR_ORDERS) else "GRB"
    if rgbw is True:
        return name + "W"
    return name


def outputs_from_cfg(cfg: object) -> list[dict]:
    if not isinstance(cfg, dict):
        return []
    hardware = cfg.get("hw") if isinstance(cfg.get("hw"), dict) else {}
    led = hardware.get("led") if isinstance(hardware, dict) and isinstance(hardware.get("led"), dict) else {}
    raw = led.get("ins") if isinstance(led, dict) else None
    outputs: list[dict] = []
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
            outputs.append(
                {
                    "index": len(outputs),
                    "start": start,
                    "pixelCount": length,
                    "pin": pin,
                    "colorOrder": color_order_name(item.get("order"), item.get("rgbw")),
                }
            )
            cursor = start + length
    if not outputs:
        total = led.get("total") if isinstance(led, dict) else None
        if isinstance(total, int) and not isinstance(total, bool) and total > 0:
            outputs.append(
                {"index": 0, "start": 0, "pixelCount": total, "pin": None, "colorOrder": "GRB"}
            )
    return outputs


def latest_cfg(fleet, mac: str) -> dict | None:
    row = fleet.db.latest_backup(mac)
    if row is None:
        return None
    try:
        raw = fleet.backups.read_blob("cfg", row["cfg_sha"])
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def list_controllers(fleet) -> list[dict]:
    records = []
    for device in fleet.db.list_devices():
        if device.get("forgotten"):
            continue
        outputs = outputs_from_cfg(latest_cfg(fleet, device["mac"]))
        if not outputs and isinstance(device.get("led_count"), int) and device["led_count"] > 0:
            outputs = [
                {
                    "index": 0,
                    "start": 0,
                    "pixelCount": int(device["led_count"]),
                    "pin": None,
                    "colorOrder": "GRB",
                }
            ]
        records.append(
            {
                "id": device["mac"],
                "name": device["name"],
                "ip": device["ip"],
                "port": int(device["port"]),
                "online": bool(device["online"]),
                "arch": device.get("arch"),
                "version": device.get("version"),
                "outputs": outputs,
            }
        )
    return records


def channels_for_order(order: str | None) -> int:
    if isinstance(order, str) and order.endswith("W"):
        return 4
    return 3
