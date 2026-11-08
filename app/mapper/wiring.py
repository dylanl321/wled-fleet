"""Controller, protocol, and start channel for an export.

The numbers come from the fleet registry. A run's startIndex is the first
pixel on its own output. The output's hw.led.ins start is added, then turned
into a 1-based DDP channel.
"""

from __future__ import annotations

from app.discovery import normalize_mac
from app.mapper.address import bus_start
from app.mapper.controllers import channels_for_order


def wiring_block(
    *,
    controller_id: str,
    output: int,
    start_index: int,
    pixel_count: int,
    color_order: str,
    controllers: list[dict] | None,
) -> dict:
    record = _record(controllers, controller_id)
    output_info = _output(record, output)
    order = str(output_info.get("colorOrder")) if output_info else color_order
    channels = channels_for_order(order or "GRB")
    known = bus_start(controllers or [], controller_id, output) if controllers else None
    origin = known if known is not None else 0
    count = int(output_info["pixelCount"]) if output_info else int(pixel_count)
    return {
        "controller": str(record["name"]) if record else controller_id,
        "ip": str(record["ip"]) if record else "",
        "protocol": "DDP",
        "output": int(output),
        "startChannel": (origin + max(0, int(start_index))) * channels + 1,
        "pixelCount": max(0, count),
        "channels": channels,
        "known": known is not None,
    }


def wiring_attrs(block: dict) -> dict[str, str]:
    return {
        "controller": str(block["controller"]),
        "ip": str(block["ip"]),
        "protocol": "DDP",
        "output": str(int(block["output"])),
        "startChannel": str(int(block["startChannel"])),
        "pixelCount": str(int(block["pixelCount"])),
    }


def _record(controllers: list[dict] | None, controller_id: str) -> dict | None:
    if not controllers:
        return None
    wanted = normalize_mac(controller_id) or controller_id
    for controller in controllers:
        ident = str(controller.get("id") or "")
        if ident == controller_id or (normalize_mac(ident) or ident) == wanted:
            return controller
        if str(controller.get("name") or "") == controller_id:
            return controller
    return None


def _output(record: dict | None, output: int) -> dict | None:
    if record is None:
        return None
    for item in record.get("outputs") or []:
        if int(item.get("index") or 0) == int(output):
            return item
    return None
