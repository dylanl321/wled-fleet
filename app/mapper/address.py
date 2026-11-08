"""Controller-wide pixel addresses.

A run's startIndex is the first pixel on its own output, usually 0. WLED
numbers the whole board from the start of output 0, so DDP, ledmaps, and
xLights need that output's hw.led.ins start added on.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from app.discovery import normalize_mac
from app.mapper.compile import Pixel
from app.mapper.controllers import list_controllers


def _same(left: str, right: str) -> bool:
    a, b = normalize_mac(left), normalize_mac(right)
    if a and b:
        return a == b
    return left == right


def bus_start(controllers: list[dict], controller_id: str, output: int) -> int | None:
    """Hardware start of one output, or None when this fleet record does not say."""
    for controller in controllers:
        if not _same(str(controller.get("id") or ""), controller_id):
            continue
        for item in controller.get("outputs") or []:
            if int(item.get("index") or 0) == int(output):
                return int(item.get("start") or 0)
        return None
    return None


def absolute_pixels(pixels: list[Pixel], controllers: list[dict]) -> list[Pixel]:
    """Rewrite each pixel index into the controller's address space.

    Known outputs use hw.led.ins start. Outputs the fleet has not described
    are stacked after the previous output on that same controller, so two
    runs that both start at 0 do not land on the same pixels.
    """
    keys: list[tuple[str, int]] = []
    for pixel in pixels:
        key = (pixel.controller, pixel.output)
        if key not in keys:
            keys.append(key)
    starts: dict[tuple[str, int], int | None] = {
        key: bus_start(controllers, key[0], key[1]) for key in keys
    }
    by_controller: dict[str, list[int]] = {}
    for controller, output in keys:
        by_controller.setdefault(controller, []).append(output)
    for controller, outputs in by_controller.items():
        cursor = 0
        for output in sorted(outputs):
            key = (controller, output)
            local_max = max(
                (pixel.index for pixel in pixels if pixel.controller == controller and pixel.output == output),
                default=-1,
            )
            span = local_max + 1
            if starts[key] is None:
                starts[key] = cursor
            cursor = max(cursor, int(starts[key] or 0) + span)
    return [
        replace(pixel, index=int(starts[(pixel.controller, pixel.output)] or 0) + pixel.index)
        for pixel in pixels
    ]


def convert_start_indices(document: dict, controllers: list[dict]) -> dict | None:
    """Turn a fixture saved with controller-wide startIndex into per-output indices.

    The old app copied hw.led.ins[].start into startIndex. A value at least as
    large as that output's hardware start is that old offset; subtract it.
    A startIndex of 0, or one below the hardware start, is already per-output.
    Returns None when nothing changes. Safe to run again.
    """
    changed = False
    runs = []
    for run in document.get("runs") or []:
        if not isinstance(run, dict):
            runs.append(run)
            continue
        updated = dict(run)
        found = bus_start(controllers, str(run.get("controllerId") or ""), int(run.get("outputIndex") or 0))
        current = int(run.get("startIndex") or 0)
        if found is not None and found > 0 and current >= found:
            updated["startIndex"] = current - found
            changed = True
        runs.append(updated)
    if not changed:
        return None
    converted = dict(document)
    converted["runs"] = runs
    return converted


def migrate_saved_fixtures(store, fleet, data_dir: Path) -> int:
    """Rewrite stored fixtures that used controller-wide startIndex. Originals are copied once."""
    controllers = list_controllers(fleet)
    backup_root = data_dir / "migration-backups" / "fixtures-before-startindex"
    rewritten = 0
    for document in store.list_documents():
        converted = convert_start_indices(document, controllers)
        if converted is None:
            continue
        fixture_id = str(document.get("id") or "")
        if fixture_id:
            dest = backup_root / f"{fixture_id}.json"
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(document, indent=2), encoding="utf-8")
        store.save(converted)
        rewritten += 1
    return rewritten
