"""Lights, Zones, and Looks on top of the existing fleet.

Device records and backups stay where they are. This module adds the names
the phone uses and the one live socket per board.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field

from app.auth import session_valid
from app.domain import (
    COMMIT_MS,
    LightRef,
    PlannedLight,
    build_marks_state,
    build_split_state,
    build_targeted_state,
    clean_cuts,
    effective_brightness,
    finder_marks,
    heal_state,
    migration_plan,
    plan_split,
    resolve_look,
    sync_plan,
)
from app.errors import FleetError, WledError
from app.library import STARTER_LOOKS
from app.livehub import LiveHub
from app.sections import buses_from_cfg
from app.util import clean_name

log = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"WLED[_-]v?(\d+(?:\.\d+){1,3})", re.IGNORECASE)


class ApplyIn(BaseModel):
    light_ids: list[int] = Field(default_factory=list, max_length=200)
    kind: str = "commit"
    look_id: int | None = None
    look: dict | None = None
    on: bool | None = None
    bri: int | None = Field(default=None, ge=0, le=255)
    tt_ms: int | None = Field(default=None, ge=0, le=60000)


class LightPatch(BaseModel):
    name: str | None = Field(default=None, max_length=32)
    zone_id: int | None = None
    clear_zone: bool = False


class ZoneIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    light_ids: list[int] = Field(default_factory=list, max_length=200)


class ZonePatch(BaseModel):
    name: str = Field(min_length=1, max_length=32)


class OrderIn(BaseModel):
    lights: list[dict] = Field(default_factory=list, max_length=400)
    zones: list[dict] = Field(default_factory=list, max_length=100)


class SpanIn(BaseModel):
    output: int = Field(ge=0, le=32)
    start: int = Field(ge=0, le=8192)
    stop: int = Field(ge=1, le=8192)
    only: bool = False


class OutputIn(BaseModel):
    output: int = Field(ge=0, le=32)


class LengthIn(BaseModel):
    output: int = Field(ge=0, le=32)
    length: int = Field(ge=1, le=8192)


class ClaimIn(BaseModel):
    output: int = Field(ge=0, le=32)
    light_id: int | None = None
    name: str = Field(min_length=1, max_length=32)
    zone_id: int | None = None
    zone_name: str | None = Field(default=None, max_length=32)


class SplitPreviewIn(BaseModel):
    output: int = Field(ge=0, le=32)
    cuts: list[int] = Field(default_factory=list, max_length=31)
    marker: int | None = Field(default=None, ge=0, le=8192)


class SplitIn(BaseModel):
    output: int = Field(ge=0, le=32)
    cuts: list[int] = Field(default_factory=list, max_length=31)
    names: list[str] = Field(default_factory=list, max_length=32)


class LookSaveIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    effect: str = Field(min_length=1, max_length=48)
    fallback: str = Field(default="Solid", max_length=48)
    palette: str = Field(default="Default", max_length=48)
    colors: list[list[int]] = Field(min_length=1, max_length=3)
    sx: int = Field(default=128, ge=0, le=255)
    ix: int = Field(default=128, ge=0, le=255)


class SettingsPatch(BaseModel):
    mirror_looks: bool | None = None
    property_name: str | None = Field(default=None, max_length=32)


class FirmwareIn(BaseModel):
    macs: list[str] = Field(default_factory=list, max_length=100)


class Lumen:
    def __init__(self, fleet):
        self.fleet = fleet
        self.db = fleet.db
        self.hub = LiveHub(self)
        self.runtime: dict[int, dict] = {}
        self.started = time.time()
        self._stop = asyncio.Event()
        self._flash: dict[str, asyncio.Task] = {}
        self._saved_flash: dict[str, dict] = {}
        self._saved_split: dict[str, dict] = {}
        self._healed: dict[str, float] = {}

    async def run(self) -> None:
        await asyncio.to_thread(self.prepare)
        self.fleet.after_scan = self.sync_from_fleet
        self.hub.start()
        try:
            await self.fleet.scan_once(full=False)
        except Exception:
            log.exception("lumen initial scan failed")
            await self.sync_from_fleet()
        self._ensure_links()
        await self._stop.wait()

    def stop(self) -> None:
        self._stop.set()
        for task in self._flash.values():
            task.cancel()
        self.hub.stop()

    def prepare(self) -> None:
        if self.db.get_setting("lumen_migrated") != "1":
            self._snapshot_database()
            self._migrate()
            self.db.set_setting("lumen_migrated", "1")
        self._seed_looks()

    def _snapshot_database(self) -> None:
        devices = self.db._one("SELECT COUNT(*) AS n FROM devices")
        backups = self.db._one("SELECT COUNT(*) AS n FROM backups")
        if int(devices["n"] if devices else 0) == 0 and int(backups["n"] if backups else 0) == 0:
            return
        dest = Path(self.db.path).with_name("fleet.db.pre-lumen")
        if dest.exists():
            return
        self.db.backup_to(dest)
        log.info("saved pre-lumen database at %s", dest)

    def _migrate(self) -> None:
        buses: dict[str, list[dict]] = {}
        for device in self.db.list_devices():
            buses[device["mac"]] = self._buses_from_backup(device["mac"])
        plan = migration_plan(self.db.list_devices(), self.db.list_groups(), buses)
        keys: dict[str, int] = {}
        for zone in plan.zones:
            created = self.db.create_zone(zone.name, zone.position)
            keys[zone.key] = int(created["id"])
        for light in plan.lights:
            self._insert_planned(light, keys.get(light.zone_key) if light.zone_key else None)

    def _buses_from_backup(self, mac: str) -> list[dict]:
        row = self.db.latest_backup(mac)
        if row is None:
            return []
        try:
            cfg = json.loads(self.fleet.backups.read_blob("cfg", row["cfg_sha"]))
        except (OSError, json.JSONDecodeError, UnicodeError, AttributeError, TypeError):
            return []
        return buses_from_cfg(cfg)

    def _insert_planned(self, light: PlannedLight, zone_id: int | None) -> None:
        self.db.insert_light(
            {
                "mac": light.mac,
                "segment_id": light.segment_id,
                "output_index": light.output_index,
                "name": light.name,
                "zone_id": zone_id,
                "position": light.position,
                "named": light.named,
                "start": light.start,
                "stop": light.stop,
            }
        )

    def _seed_looks(self) -> None:
        if self.db.get_setting("lumen_looks_seeded") == "1":
            return
        if not self.db.list_looks():
            for index, look in enumerate(STARTER_LOOKS):
                self.db.insert_look({**look, "builtin": True, "position": index})
        self.db.set_setting("lumen_looks_seeded", "1")

    async def sync_from_fleet(self) -> None:
        for device in self.db.list_devices():
            if device.get("forgotten"):
                continue
            try:
                await self.sync_device(device)
            except Exception:
                log.info("could not sync lights for %s", device.get("name"), exc_info=False)
        self._ensure_links()

    async def sync_device(self, device: dict) -> None:
        buses = await self._live_buses(device)
        plan = sync_plan(self.db.lights_for_mac(device["mac"]), buses, device["name"], device.get("led_count"))
        for light_id in plan["delete_ids"]:
            self.db.delete_light(light_id)
            self.runtime.pop(light_id, None)
        for row in plan["upserts"]:
            if row["id"] is None:
                self.db.insert_light(
                    {
                        "mac": device["mac"],
                        "segment_id": row["segment_id"],
                        "output_index": row["output_index"],
                        "name": row["name"],
                        "zone_id": row.get("zone_id"),
                        "position": row.get("position") or 0,
                        "named": row["named"],
                        "start": row["start"],
                        "stop": row["stop"],
                    }
                )
            else:
                self.db.update_light(
                    int(row["id"]),
                    {
                        "segment_id": row["segment_id"],
                        "start": row["start"],
                        "stop": row["stop"],
                    },
                )

    async def _live_buses(self, device: dict) -> list[dict]:
        try:
            raw = await self.fleet.wled.get_cfg_bytes(device)
            return buses_from_cfg(json.loads(raw))
        except (WledError, json.JSONDecodeError, UnicodeError, TypeError):
            return self._buses_from_backup(device["mac"])

    def _ensure_links(self) -> None:
        seen: set[str] = set()
        for light in self.db.list_lights():
            seen.add(light["mac"])
        for mac in seen:
            device = self.db.get_device(mac)
            if device is None or device.get("forgotten"):
                continue
            self.hub.ensure(mac, device.get("arch"))

    def lights_for_mac(self, mac: str) -> list[dict]:
        return self.db.lights_for_mac(mac)

    async def refresh_catalog(self, link) -> None:
        device = self.db.get_device(link.mac)
        if device is None:
            return
        try:
            effects = await self.fleet.wled.get_json_any(device, "/json/eff", timeout=2.5)
            palettes = await self.fleet.wled.get_json_any(device, "/json/pal", timeout=2.5)
        except WledError:
            return
        if isinstance(effects, list):
            link.effects = [item for item in effects if isinstance(item, str)]
        if isinstance(palettes, list):
            link.palettes = [item for item in palettes if isinstance(item, str)]
        try:
            state = await self.fleet.wled.get_json(device, "/json/state", timeout=2.5)
        except WledError:
            return
        self.ingest_state(link.mac, state)

    def ingest_state(self, mac: str, state: dict) -> None:
        if not isinstance(state, dict):
            return
        self._heal(mac, state)
        master_on = bool(state.get("on"))
        master_bri = state.get("bri") if isinstance(state.get("bri"), int) else 0
        raw_segments = state.get("seg") if isinstance(state.get("seg"), list) else []
        link = self.hub.links.get(mac)
        changed = []
        lights = self.db.lights_for_mac(mac)
        for index, light in enumerate(lights):
            segment = {}
            for item in raw_segments:
                if isinstance(item, dict) and isinstance(item.get("id"), int) and item["id"] == int(light["segment_id"]):
                    segment = item
                    break
            if not segment and len(lights) == 1:
                for item in raw_segments:
                    if isinstance(item, dict):
                        segment = item
                        break
            seg_on = segment.get("on") is not False
            colors = []
            for color in segment.get("col") or []:
                if isinstance(color, list) and len(color) >= 3:
                    colors.append([int(color[0]), int(color[1]), int(color[2])])
            while len(colors) < 3:
                colors.append([0, 0, 0])
            fx = segment.get("fx") if isinstance(segment.get("fx"), int) else 0
            pal = segment.get("pal") if isinstance(segment.get("pal"), int) else 0
            effect = link.effects[fx] if link and 0 <= fx < len(link.effects) else ""
            palette = link.palettes[pal] if link and 0 <= pal < len(link.palettes) else ""
            seg_bri = segment.get("bri") if isinstance(segment.get("bri"), int) else None
            runtime = {
                "on": bool(master_on and seg_on),
                "bri": effective_brightness(master_bri, seg_bri),
                "colors": colors,
                "fx": fx,
                "pal": pal,
                "sx": segment.get("sx") if isinstance(segment.get("sx"), int) else 128,
                "ix": segment.get("ix") if isinstance(segment.get("ix"), int) else 128,
                "effect": effect,
                "palette": palette,
            }
            self.runtime[int(light["id"])] = runtime
            changed.append(self._public_light(light, brief=True))
        if changed:
            self.hub.publish_state(changed)

    def _refs(self, mac: str) -> list[LightRef]:
        refs = []
        for light in self.db.lights_for_mac(mac):
            runtime = self.runtime.get(int(light["id"]), {})
            refs.append(
                LightRef(
                    id=int(light["id"]),
                    mac=mac,
                    segment_id=int(light["segment_id"]),
                    start=int(light["start"]),
                    stop=int(light["stop"]),
                    on=bool(runtime.get("on", True)),
                    bri=int(runtime.get("bri", 255)),
                )
            )
        return refs

    async def apply(self, body: ApplyIn) -> dict:
        if body.kind not in {"preview", "commit"}:
            raise FleetError("Unknown apply kind.")
        lights = [self._require_light(light_id) for light_id in body.light_ids]
        if not lights:
            raise FleetError("Choose a light.")
        look = self._look_body(body)
        if body.kind == "commit" and body.look_id:
            self.db.touch_look(body.look_id)
        groups: dict[str, list[dict]] = {}
        for light in lights:
            groups.setdefault(light["mac"], []).append(light)
        results = []
        mirror = self._mirror_enabled() and body.kind == "commit" and look is not None
        for mac, chosen in groups.items():
            device = self.fleet._require(mac)
            link = self.hub.links.get(mac)
            effects = link.effects if link else []
            palettes = link.palettes if link else []
            resolved = resolve_look(look, effects, palettes) if look else None
            tt_ms = body.tt_ms
            if tt_ms is None:
                tt_ms = 0 if body.kind == "preview" else COMMIT_MS
            payload = build_targeted_state(
                self._refs(mac),
                {int(light["id"]) for light in chosen},
                on=body.on,
                bri=body.bri,
                col=resolved["colors"] if resolved else None,
                fx=resolved["fx"] if resolved else None,
                pal=resolved["pal"] if resolved else None,
                sx=resolved["sx"] if resolved else None,
                ix=resolved["ix"] if resolved else None,
                tt_ms=tt_ms,
            )
            if mirror and body.look_id:
                payload["psave"] = 100 + (int(body.look_id) % 120)
                payload["n"] = look.get("name") or "Look"
                payload["ib"] = False
                payload["sb"] = False
            decision = self.hub.submit(mac, device.get("arch"), body.kind, payload)
            if body.kind == "commit" and decision.get("send") is not None:
                link = self.hub.links.get(mac)
                if link is not None and not link.connected:
                    link.offline_commit = decision["send"]
            self._remember(chosen, body, resolved)
            results.append(
                {
                    "mac": mac,
                    "seq": decision.get("seq"),
                    "sent": decision.get("send") is not None,
                    "fallback": bool(resolved and resolved["fallback"]),
                    "effect": resolved["effect"] if resolved else None,
                }
            )
        return {"ok": True, "results": results}

    def _remember(self, lights: list[dict], body: ApplyIn, resolved: dict | None) -> None:
        for light in lights:
            runtime = self.runtime.setdefault(int(light["id"]), {})
            if body.on is not None:
                runtime["on"] = body.on
                if body.on and not runtime.get("bri"):
                    runtime["bri"] = 255
            if body.bri is not None:
                runtime["bri"] = body.bri
                runtime["on"] = body.bri > 0
            if resolved:
                runtime["colors"] = resolved["colors"]
                runtime["fx"] = resolved["fx"]
                runtime["pal"] = resolved["pal"]
                runtime["sx"] = resolved["sx"]
                runtime["ix"] = resolved["ix"]
                runtime["effect"] = resolved["effect"]
                runtime["palette"] = resolved["palette"]
                runtime["fallback"] = resolved["fallback"]

    def _look_body(self, body: ApplyIn) -> dict | None:
        if body.look_id:
            stored = self.db.get_look(body.look_id)
            if stored is None:
                raise FleetError("Unknown look.", 404)
            look = {
                "name": stored["name"],
                "effect": stored["effect"],
                "fallback": stored["fallback"],
                "palette": stored["palette"],
                "colors": stored["colors"],
                "sx": stored["sx"],
                "ix": stored["ix"],
            }
            if body.look:
                for key in ("colors", "sx", "ix", "effect", "fallback", "palette"):
                    if key in body.look and body.look[key] is not None:
                        look[key] = body.look[key]
            return look
        if body.look:
            return body.look
        return None

    def _require_light(self, light_id: int) -> dict:
        light = self.db.get_light(int(light_id))
        if light is None:
            raise FleetError("Unknown light.", 404)
        return light

    def rename_light(self, light_id: int, name: str) -> dict:
        clean = clean_name(name)
        if not clean:
            raise FleetError("Enter a name.")
        light = self._require_light(light_id)
        updated = self.db.update_light(int(light["id"]), {"name": clean})
        assert updated is not None
        return self._public_light(updated)

    def patch_light(self, light_id: int, patch: LightPatch) -> dict:
        light = self._require_light(light_id)
        fields: dict = {}
        if patch.name is not None:
            clean = clean_name(patch.name)
            if not clean:
                raise FleetError("Enter a name.")
            fields["name"] = clean
            fields["named"] = True
        if patch.clear_zone:
            fields["zone_id"] = None
        elif patch.zone_id is not None:
            if self.db.get_zone(patch.zone_id) is None:
                raise FleetError("Unknown zone.", 404)
            fields["zone_id"] = patch.zone_id
        updated = self.db.update_light(int(light["id"]), fields) or light
        self.db.delete_empty_zones()
        return self._public_light(updated)

    def create_zone(self, name: str, light_ids: list[int]) -> dict:
        clean = clean_name(name) or "Zone"
        zone = self.db.create_zone(clean)
        for index, light_id in enumerate(light_ids):
            self._require_light(light_id)
            self.db.update_light(light_id, {"zone_id": zone["id"], "position": index})
        self.db.delete_empty_zones()
        return self.db.get_zone(int(zone["id"])) or zone

    def rename_zone(self, zone_id: int, name: str) -> dict:
        if self.db.get_zone(zone_id) is None:
            raise FleetError("Unknown zone.", 404)
        clean = clean_name(name)
        if not clean:
            raise FleetError("Enter a name.")
        self.db.rename_zone(zone_id, clean)
        return self.db.get_zone(zone_id)

    def delete_zone(self, zone_id: int) -> None:
        if self.db.get_zone(zone_id) is None:
            raise FleetError("Unknown zone.", 404)
        self.db.delete_zone(zone_id)

    def reorder(self, body: OrderIn) -> dict:
        zone_pairs = []
        for item in body.zones:
            if "id" in item and "position" in item:
                zone_pairs.append((int(item["id"]), int(item["position"])))
        if zone_pairs:
            self.db.set_zone_positions(zone_pairs)
        normalized = []
        for item in body.lights:
            if "id" not in item:
                continue
            zone_raw = item.get("zone_id")
            zone_id = int(zone_raw) if isinstance(zone_raw, int) and zone_raw > 0 else None
            normalized.append({"id": int(item["id"]), "zone_id": zone_id, "position": int(item.get("position") or 0)})
        if normalized:
            self.db.set_light_order(normalized)
        self.db.delete_empty_zones()
        return self.home()

    async def span(self, mac: str, body: SpanIn) -> dict:
        device = self.fleet._require(mac)
        pieces = self._output_lights(mac, body.output)
        start = int(pieces[0]["start"])
        stop = int(pieces[-1]["stop"])
        low = start + max(0, min(body.start, body.stop))
        high = min(stop, start + max(body.start, body.stop))
        payload = build_marks_state([self._segment_of(piece) for piece in pieces], [(low, max(low + 1, high), "FFE7B0")])
        if body.only:
            others = [light for light in self.db.lights_for_mac(mac) if int(light["output_index"]) != body.output]
            payload["seg"].extend({"id": int(light["segment_id"]), "on": False} for light in others)
        self.hub.submit(mac, device.get("arch"), "commit", payload)
        return {"ok": True, "count": abs(body.stop - body.start)}

    # ----- segment finder ---------------------------------------------------

    def _output_lights(self, mac: str, output: int) -> list[dict]:
        pieces = sorted(
            (light for light in self.db.lights_for_mac(mac) if int(light["output_index"]) == output),
            key=lambda light: (int(light["start"]), int(light["id"])),
        )
        if not pieces:
            raise FleetError("That output is not on this board.", 404)
        return pieces

    @staticmethod
    def _segment_of(light: dict) -> dict:
        return {"id": int(light["segment_id"]), "start": int(light["start"]), "stop": int(light["stop"])}

    def _max_segments(self, device: dict) -> int:
        try:
            info = json.loads(device.get("info_json") or "{}")
            value = info.get("leds", {}).get("maxseg")
        except (ValueError, AttributeError):
            value = None
        return int(value) if isinstance(value, int) and value > 0 else 16

    async def _hold_for_finder(self, mac: str, device: dict) -> None:
        if mac in self._saved_split:
            return
        try:
            self._saved_split[mac] = await self.fleet.wled.get_json(device, "/json/state", timeout=2.5)
        except WledError as exc:
            raise FleetError(str(exc)) from exc

    def _restore_segments(self, mac: str) -> dict | None:
        saved = self._saved_split.pop(mac, None)
        if not saved:
            return None
        body = {"tt": 0, **_restore_body(saved)}
        body["seg"] = [{**segment, "frz": False} for segment in body.get("seg") or [] if isinstance(segment, dict)]
        return body

    async def split_preview(self, mac: str, body: SplitPreviewIn) -> dict:
        """Show the proposed pieces and the marker on the real strip without changing its segments."""
        device = self.fleet._require(mac)
        pieces = self._output_lights(mac, body.output)
        start = int(pieces[0]["start"])
        stop = int(pieces[-1]["stop"])
        await self._hold_for_finder(mac, device)
        cuts = [start + int(cut) for cut in body.cuts]
        marker = start + body.marker if body.marker is not None else None
        payload = build_marks_state([self._segment_of(piece) for piece in pieces], finder_marks(start, stop, cuts, marker))
        self.hub.submit(mac, device.get("arch"), "preview", payload)
        return {"ok": True, "pieces": len(clean_cuts(cuts, start, stop)) + 1}

    async def split_cancel(self, mac: str) -> dict:
        device = self.fleet._require(mac)
        restore = self._restore_segments(mac)
        if restore:
            self.hub.submit(mac, device.get("arch"), "commit", restore)
        return {"ok": True}

    async def split(self, mac: str, body: SplitIn) -> dict:
        """Cut one output into several Lights, or merge it back with no cuts."""
        device = self.fleet._require(mac)
        pieces = self._output_lights(mac, body.output)
        start = int(pieces[0]["start"])
        stop = int(pieces[-1]["stop"])
        cuts = clean_cuts([start + int(cut) for cut in body.cuts], start, stop)
        board_lights = self.db.lights_for_mac(mac)
        output_ids = {int(piece["id"]) for piece in pieces}
        saved = self._saved_split.get(mac) or {}
        taken = {int(light["segment_id"]) for light in board_lights if int(light["id"]) not in output_ids}
        taken |= {int(piece["segment_id"]) for piece in pieces}
        taken |= {
            int(segment["id"])
            for segment in saved.get("seg") or []
            if isinstance(segment, dict) and isinstance(segment.get("id"), int)
            and not any(int(segment["id"]) == int(piece["segment_id"]) for piece in pieces)
        }
        try:
            planned, leftover = plan_split(pieces, start, stop, cuts, taken, self._max_segments(device))
        except ValueError as exc:
            raise FleetError(str(exc)) from exc
        template = None
        for segment in saved.get("seg") or []:
            if isinstance(segment, dict) and segment.get("id") == int(pieces[0]["segment_id"]):
                template = segment
        payload = build_split_state(planned, [int(light["segment_id"]) for light in leftover], template)
        restore = self._restore_segments(mac)
        if restore:
            # One write: the board's look comes back and the new layout lands together.
            merged: dict[int, dict] = {int(segment["id"]): segment for segment in restore["seg"] if isinstance(segment.get("id"), int)}
            for segment in payload["seg"]:
                merged[int(segment["id"])] = {**merged.get(int(segment["id"]), {}), **segment}
            payload = {**restore, "seg": list(merged.values())}
        self.hub.submit(mac, device.get("arch"), "commit", payload)

        base = pieces[0]
        names = [clean_name(name) for name in body.names]
        for index, piece in enumerate(planned):
            name = names[index] if index < len(names) and names[index] else None
            if piece.light_id is not None:
                fields: dict = {"start": piece.start, "stop": piece.stop, "segment_id": piece.segment_id}
                if name:
                    fields["name"] = name
                self.db.update_light(piece.light_id, fields)
                continue
            self.db.insert_light(
                {
                    "mac": mac,
                    "segment_id": piece.segment_id,
                    "output_index": int(body.output),
                    "name": (name or f"{base['name']} {index + 1}")[:32],
                    "zone_id": base.get("zone_id"),
                    "position": int(base.get("position") or 0) + index,
                    "named": bool(base.get("named")),
                    "start": piece.start,
                    "stop": piece.stop,
                }
            )
        for light in leftover:
            self.db.delete_light(int(light["id"]))
            self.runtime.pop(int(light["id"]), None)
        self.db.delete_empty_zones()
        result = self.home()
        result["split"] = [{"start": piece.start - start, "stop": piece.stop - start, "segment_id": piece.segment_id} for piece in planned]
        return result

    def _heal(self, mac: str, state: dict) -> None:
        """Put back segments a split board lost, e.g. after it rebooted without a boot preset."""
        if mac in self._saved_split:
            return
        payload = heal_state(self.db.lights_for_mac(mac), state)
        if payload is None:
            return
        now = time.monotonic()
        if now - self._healed.get(mac, 0.0) < 10:
            return
        self._healed[mac] = now
        device = self.db.get_device(mac)
        if device is None:
            return
        log.info("restoring %s segment(s) on %s", len(payload["seg"]), device.get("name"))
        self.hub.submit(mac, device.get("arch"), "commit", payload)

    async def light_only(self, mac: str, output: int) -> dict:
        device = self.fleet._require(mac)
        lights = self.db.lights_for_mac(mac)
        selected = {int(light["id"]) for light in lights if int(light["output_index"]) == output}
        if not selected:
            raise FleetError("That output is not on this board.", 404)
        # Address every output: the chosen one on, the others off, master on.
        refs = self._refs(mac)
        segments = []
        for light in refs:
            segment = {
                "id": light.segment_id,
                "start": light.start,
                "stop": max(light.start + 1, light.stop),
                "on": light.id in selected,
            }
            if light.id in selected:
                segment["fx"] = 0
                segment["col"] = [[255, 244, 214], [0, 0, 0], [0, 0, 0]]
                segment["bri"] = 255
            segments.append(segment)
        payload = {"tt": 0, "on": True, "bri": 255, "seg": segments}
        self.hub.submit(mac, device.get("arch"), "commit", payload)
        return {"ok": True}

    async def set_length(self, mac: str, body: LengthIn) -> dict:
        device = self.fleet._require(mac)
        buses = await self._live_buses(device)
        if not buses:
            buses = buses_from_cfg({})
        if not buses:
            raise FleetError("This controller has no LED outputs in its config.")
        packed = []
        cursor = 0
        found = False
        for bus in buses:
            length = body.length if int(bus["index"]) == body.output else int(bus["len"])
            if int(bus["index"]) == body.output:
                found = True
            packed.append({"start": cursor, "len": length})
            cursor += length
        if not found:
            raise FleetError("That output is not on this board.", 404)
        await self.fleet.set_leds(mac, packed)
        fresh = self.fleet._require(mac)
        await self.sync_device(fresh)
        return {"ok": True, "length": body.length}

    async def claim(self, mac: str, body: ClaimIn) -> dict:
        self.fleet._require(mac)
        lights = self.db.lights_for_mac(mac)
        if body.light_id is not None:
            match = next((light for light in lights if int(light["id"]) == body.light_id), None)
        else:
            match = next((light for light in lights if int(light["output_index"]) == body.output), None)
        if match is None:
            raise FleetError("That output is not on this board.", 404)
        zone_id = body.zone_id
        if body.zone_name:
            zone = self.db.create_zone(clean_name(body.zone_name) or "Zone")
            zone_id = int(zone["id"])
        if zone_id is not None and self.db.get_zone(zone_id) is None:
            raise FleetError("Unknown zone.", 404)
        clean = clean_name(body.name)
        if not clean:
            raise FleetError("Enter a name.")
        fields: dict = {"name": clean, "named": True}
        if zone_id is not None:
            fields["zone_id"] = zone_id
        updated = self.db.update_light(int(match["id"]), fields)
        assert updated is not None
        return self._public_light(updated)

    async def toggle_flash(self, mac: str) -> dict:
        self.fleet._require(mac)
        current = self._flash.get(mac)
        if current is not None:
            current.cancel()
            self._flash.pop(mac, None)
            saved = self._saved_flash.pop(mac, None)
            if saved:
                device = self.fleet._require(mac)
                self.hub.submit(mac, device.get("arch"), "commit", {"tt": 0, **_restore_body(saved)})
            return {"flashing": False}
        device = self.fleet._require(mac)
        try:
            self._saved_flash[mac] = await self.fleet.wled.get_json(device, "/json/state", timeout=2.5)
        except WledError:
            self._saved_flash[mac] = {}
        self._flash[mac] = asyncio.create_task(self._flash_loop(mac))
        return {"flashing": True}

    async def _flash_loop(self, mac: str) -> None:
        white = True
        try:
            while True:
                device = self.db.get_device(mac)
                if device is None:
                    return
                refs = self._refs(mac)
                if white:
                    payload = build_targeted_state(
                        refs,
                        {light.id for light in refs},
                        on=True,
                        bri=255,
                        col=[[255, 255, 255], [0, 0, 0], [0, 0, 0]],
                        fx=0,
                        tt_ms=0,
                    )
                else:
                    payload = build_targeted_state(refs, {light.id for light in refs}, on=True, bri=0, tt_ms=0)
                self.hub.submit(mac, device.get("arch"), "commit", payload)
                white = not white
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            return

    async def identify(self, light_id: int) -> dict:
        light = self._require_light(light_id)
        device = self.fleet._require(light["mac"])
        refs = self._refs(light["mac"])
        target = {int(light["id"])}
        previous = dict(self.runtime.get(int(light["id"]), {}))
        solid = resolve_look({"effect": "Solid", "fallback": "Solid", "palette": "Default", "colors": [[255, 255, 255], [0, 0, 0], [0, 0, 0]], "sx": 0, "ix": 0}, self._effects(light["mac"]), self._palettes(light["mac"]))
        for _ in range(3):
            on = build_targeted_state(refs, target, on=True, col=solid["colors"], fx=solid["fx"], bri=255, tt_ms=0)
            self.hub.submit(light["mac"], device.get("arch"), "commit", on)
            await asyncio.sleep(0.2)
            dim = build_targeted_state(
                refs,
                target,
                on=True,
                bri=0,
                col=previous.get("colors"),
                fx=previous.get("fx"),
                tt_ms=0,
            )
            self.hub.submit(light["mac"], device.get("arch"), "commit", dim)
            await asyncio.sleep(0.2)
        restore = build_targeted_state(
            refs,
            target,
            on=previous.get("on", True),
            bri=previous.get("bri", 128),
            col=previous.get("colors"),
            fx=previous.get("fx"),
            pal=previous.get("pal"),
            sx=previous.get("sx"),
            ix=previous.get("ix"),
            tt_ms=0,
        )
        self.hub.submit(light["mac"], device.get("arch"), "commit", restore)
        return {"ok": True}

    def _effects(self, mac: str) -> list[str]:
        link = self.hub.links.get(mac)
        return link.effects if link else []

    def _palettes(self, mac: str) -> list[str]:
        link = self.hub.links.get(mac)
        return link.palettes if link else []

    def save_look(self, body: LookSaveIn) -> dict:
        colors = []
        for color in body.colors[:3]:
            if len(color) < 3:
                raise FleetError("Colors need red, green, and blue.")
            colors.append([max(0, min(255, int(color[0]))), max(0, min(255, int(color[1]))), max(0, min(255, int(color[2])))])
        while len(colors) < 3:
            colors.append([0, 0, 0])
        position = len(self.db.list_looks())
        return self._public_look(
            self.db.insert_look(
                {
                    "name": clean_name(body.name) or body.name.strip()[:32],
                    "effect": body.effect.strip(),
                    "fallback": body.fallback.strip() or "Solid",
                    "palette": body.palette.strip() or "Default",
                    "colors": colors,
                    "sx": body.sx,
                    "ix": body.ix,
                    "builtin": False,
                    "position": position,
                }
            )
        )

    def update_settings(self, body: SettingsPatch) -> dict:
        if body.mirror_looks is not None:
            self.db.set_setting("mirror_looks", "1" if body.mirror_looks else "0")
        if body.property_name is not None:
            self.db.set_setting("property_name", clean_name(body.property_name))
        return self.home()["server"]

    async def rescan(self) -> dict:
        summary = await self.fleet.scan_once(full=True)
        return {"ok": True, "scan": summary}

    def queue_firmware(self, macs: list[str]) -> dict:
        chosen = macs or [device["mac"] for device in self.db.list_devices() if not device.get("forgotten")]
        pairs: list[tuple[str, int]] = []
        for mac in chosen:
            device = self.fleet._require(mac)
            target = self._firmware_target(device)
            if target is None:
                continue
            pairs.append((mac, int(target["bin_id"])))
        if not pairs:
            raise FleetError("No firmware is assigned to those controllers.")

        async def work(job) -> None:
            for mac, bin_id in pairs:
                name = mac
                try:
                    device = self.fleet._require(mac)
                    name = device["name"]
                    result = await self.fleet.update_firmware(mac, bin_id, force=False)
                    job.results.append({"mac": mac, "name": name, "ok": True, "version": result.get("version")})
                except FleetError as exc:
                    job.results.append({"mac": mac, "name": name, "ok": False, "error": str(exc)})
                job.done += 1

        return self.fleet.start_job("firmware", len(pairs), work)

    def home(self) -> dict:
        zones = self.db.list_zones()
        lights = [self._public_light(light) for light in self.db.list_lights()]
        named = [light for light in lights if light["named"]]
        pending = [light for light in lights if not light["named"]]
        attention = self._attention()
        return {
            "property": self._property(),
            "server": self._server(),
            "zones": [{"id": zone["id"], "name": zone["name"], "position": zone["position"]} for zone in zones],
            "lights": named,
            "pending": pending,
            "looks": [self._public_look(look) for look in self._ordered_looks()],
            "attention": attention,
        }

    def maintenance(self) -> dict:
        attention = self._attention_lists()
        boards = []
        backups = []
        for device in self.db.list_devices():
            if device.get("forgotten"):
                continue
            outputs = self.db.lights_for_mac(device["mac"])
            boards.append(self._public_board(device, outputs))
            backups.append(
                {
                    "mac": device["mac"],
                    "name": device["name"],
                    "items": [
                        {
                            "id": row["id"],
                            "created_at": row["created_at"],
                            "reason": row["reason"],
                            "version": row.get("version"),
                            "led_count": row.get("led_count"),
                            "name": row.get("name"),
                        }
                        for row in self.db.list_backups(device["mac"])
                    ],
                }
            )
        return {
            "needs": attention,
            "boards": boards,
            "backups": backups,
            "server": self._server(),
            "jobs": [job.public() for job in list(self.fleet.jobs.values())[:8]],
        }

    def _ordered_looks(self) -> list[dict]:
        looks = self.db.list_looks()
        solid = [look for look in looks if str(look["name"]).lower() == "solid" or str(look["effect"]).lower() == "solid" and look.get("builtin")]
        # Pin the builtin Solid, not a user look that happens to use the effect.
        pinned = [look for look in looks if look.get("builtin") and str(look["effect"]).lower() == "solid" and str(look["name"]).lower() == "solid"]
        pinned_ids = {look["id"] for look in pinned}
        rest = [look for look in looks if look["id"] not in pinned_ids]
        rest.sort(key=lambda look: (-int(look["use_count"]), int(look["position"]), int(look["id"])))
        return pinned + rest

    def _attention(self) -> dict:
        lists = self._attention_lists()
        macs = {item["mac"] for item in lists["offline"]}
        macs.update(item["mac"] for item in lists["pending"])
        macs.update(item["mac"] for item in lists["firmware"])
        icon = None
        if lists["firmware"]:
            icon = "up"
        elif macs:
            icon = "dot"
        return {"count": len(macs), "icon": icon, "offline": len(lists["offline"]), "pending": len(lists["pending"]), "firmware": len(lists["firmware"])}

    def _attention_lists(self) -> dict:
        offline = []
        pending = []
        firmware = []
        pending_macs: set[str] = set()
        for light in self.db.list_lights():
            if not light["named"]:
                pending_macs.add(light["mac"])
        for device in self.db.list_devices():
            if device.get("forgotten"):
                continue
            if device["mac"] in pending_macs:
                pending.append(
                    {
                        "mac": device["mac"],
                        "name": device["name"],
                        "ip": device["ip"],
                        "outputs": len(self.db.lights_for_mac(device["mac"])),
                    }
                )
            if not device.get("online") and device["mac"] not in pending_macs:
                offline.append(
                    {
                        "mac": device["mac"],
                        "name": device["name"],
                        "last_seen": device.get("last_seen"),
                    }
                )
            target = self._firmware_target(device)
            if target is not None:
                firmware.append({"mac": device["mac"], "name": device["name"], "version": device.get("version"), **target})
        return {"offline": offline, "pending": pending, "firmware": firmware}

    def _firmware_target(self, device: dict) -> dict | None:
        profile = self.db.get_profile(device.get("profile_id"))
        bin_id = device.get("bin_id") or (profile or {}).get("bin_id")
        if not bin_id:
            return None
        blob = self.db.get_bin(int(bin_id))
        if blob is None:
            return None
        match = _VERSION_RE.search(blob["filename"] or "")
        found = match.group(1) if match else None
        if not found or found == device.get("version"):
            return None
        return {"bin_id": blob["id"], "filename": blob["filename"], "target": found}

    def _property(self) -> str:
        saved = self.db.get_setting("property_name") or ""
        return saved.strip() or "All lights"

    def _mirror_enabled(self) -> bool:
        return (self.db.get_setting("mirror_looks", "1") or "1") != "0"

    def _server(self) -> dict:
        return {
            "version": self.fleet.settings.app_version,
            "uptime": int(time.time() - self.started),
            "mirror_looks": self._mirror_enabled(),
            "property": self._property(),
        }

    def _public_light(self, light: dict, brief: bool = False) -> dict:
        runtime = self.runtime.get(int(light["id"]), {})
        device = self.db.get_device(light["mac"]) or {}
        length = max(0, int(light["stop"]) - int(light["start"]))
        payload = {
            "id": light["id"],
            "name": light["name"],
            "mac": light["mac"],
            "zone_id": light.get("zone_id"),
            "position": light["position"],
            "start": light["start"],
            "stop": light["stop"],
            "length": length,
            "segment_id": light["segment_id"],
            "output_index": light["output_index"],
            "named": bool(light["named"]),
            "on": bool(runtime.get("on", True)),
            "bri": int(runtime.get("bri", 128)),
            "effect": runtime.get("effect") or "",
            "palette": runtime.get("palette") or "",
            "colors": runtime.get("colors") or [[255, 214, 170], [0, 0, 0], [0, 0, 0]],
            "sx": int(runtime.get("sx", 128)),
            "ix": int(runtime.get("ix", 128)),
            "fallback": bool(runtime.get("fallback")),
            "online": bool(device.get("online")),
        }
        if brief:
            return payload
        payload["board"] = {
            "name": device.get("name") or light["mac"],
            "ip": device.get("ip"),
            "arch": device.get("arch"),
            "version": device.get("version"),
            "chip": _chip(device.get("arch")),
        }
        return payload

    def _public_look(self, look: dict) -> dict:
        return {
            "id": look["id"],
            "name": look["name"],
            "effect": look["effect"],
            "fallback": look["fallback"],
            "palette": look["palette"],
            "colors": look["colors"],
            "sx": look["sx"],
            "ix": look["ix"],
            "builtin": bool(look["builtin"]),
            "use_count": look["use_count"],
        }

    def _public_board(self, device: dict, outputs: list[dict]) -> dict:
        return {
            "mac": device["mac"],
            "name": device["name"],
            "ip": device["ip"],
            "arch": device.get("arch"),
            "chip": _chip(device.get("arch")),
            "version": device.get("version"),
            "online": bool(device.get("online")),
            "outputs": len(outputs),
            "led_count": device.get("led_count"),
            "last_seen": device.get("last_seen"),
        }


def _chip(arch: str | None) -> str:
    if not arch:
        return ""
    if "8266" in arch:
        return "ESP8266"
    if "s3" in arch:
        return "ESP32-S3"
    if "esp32" in arch:
        return "ESP32"
    return arch


def _restore_body(state: dict) -> dict:
    keep = {}
    for key in ("on", "bri", "seg", "ps"):
        if key in state:
            keep[key] = state[key]
    return keep or {"on": True}


def mount_lumen(app: FastAPI) -> None:
    from app.main import COOKIE, fleet_of

    def lumen_of(request: Request) -> Lumen:
        return request.app.state.lumen

    def mac_of(request: Request, mac: str) -> str:
        from app.discovery import normalize_mac

        normalized = normalize_mac(mac)
        if normalized is None or fleet_of(request).db.get_device(normalized) is None:
            raise HTTPException(status_code=404, detail="Unknown controller")
        return normalized

    @app.get("/api/lumen/home")
    async def home(request: Request) -> dict:
        return lumen_of(request).home()

    @app.get("/api/lumen/maintenance")
    async def maintenance(request: Request) -> dict:
        return lumen_of(request).maintenance()

    @app.post("/api/lumen/apply")
    async def apply(body: ApplyIn, request: Request) -> dict:
        return await lumen_of(request).apply(body)

    @app.post("/api/lumen/lights/{light_id}")
    async def patch_light(light_id: int, body: LightPatch, request: Request) -> dict:
        return lumen_of(request).patch_light(light_id, body)

    @app.post("/api/lumen/lights/{light_id}/identify")
    async def identify(light_id: int, request: Request) -> dict:
        return await lumen_of(request).identify(light_id)

    @app.post("/api/lumen/zones")
    async def create_zone(body: ZoneIn, request: Request) -> dict:
        return lumen_of(request).create_zone(body.name, body.light_ids)

    @app.post("/api/lumen/zones/{zone_id}")
    async def rename_zone(zone_id: int, body: ZonePatch, request: Request) -> dict:
        zone = lumen_of(request).rename_zone(zone_id, body.name)
        return {"id": zone["id"], "name": zone["name"], "position": zone["position"]}

    @app.delete("/api/lumen/zones/{zone_id}")
    async def delete_zone(zone_id: int, request: Request) -> dict:
        lumen_of(request).delete_zone(zone_id)
        return {"ok": True}

    @app.post("/api/lumen/order")
    async def order(body: OrderIn, request: Request) -> dict:
        return lumen_of(request).reorder(body)

    @app.post("/api/lumen/find/{mac}/span")
    async def span(mac: str, body: SpanIn, request: Request) -> dict:
        return await lumen_of(request).span(mac_of(request, mac), body)

    @app.post("/api/lumen/find/{mac}/only")
    async def only(mac: str, body: OutputIn, request: Request) -> dict:
        return await lumen_of(request).light_only(mac_of(request, mac), body.output)

    @app.post("/api/lumen/find/{mac}/length")
    async def length(mac: str, body: LengthIn, request: Request) -> dict:
        return await lumen_of(request).set_length(mac_of(request, mac), body)

    @app.post("/api/lumen/find/{mac}/split/preview")
    async def split_preview(mac: str, body: SplitPreviewIn, request: Request) -> dict:
        return await lumen_of(request).split_preview(mac_of(request, mac), body)

    @app.post("/api/lumen/find/{mac}/split/cancel")
    async def split_cancel(mac: str, request: Request) -> dict:
        return await lumen_of(request).split_cancel(mac_of(request, mac))

    @app.post("/api/lumen/find/{mac}/split")
    async def split(mac: str, body: SplitIn, request: Request) -> dict:
        return await lumen_of(request).split(mac_of(request, mac), body)

    @app.post("/api/lumen/find/{mac}/flash")
    async def flash(mac: str, request: Request) -> dict:
        return await lumen_of(request).toggle_flash(mac_of(request, mac))

    @app.post("/api/lumen/find/{mac}/claim")
    async def claim(mac: str, body: ClaimIn, request: Request) -> dict:
        return await lumen_of(request).claim(mac_of(request, mac), body)

    @app.post("/api/lumen/looks")
    async def save_look(body: LookSaveIn, request: Request) -> dict:
        return lumen_of(request).save_look(body)

    @app.post("/api/lumen/settings")
    async def settings(body: SettingsPatch, request: Request) -> dict:
        return lumen_of(request).update_settings(body)

    @app.post("/api/lumen/rescan")
    async def rescan(request: Request) -> dict:
        return await lumen_of(request).rescan()

    @app.post("/api/lumen/firmware")
    async def firmware(body: FirmwareIn, request: Request) -> dict:
        return lumen_of(request).queue_firmware(body.macs)

    @app.post("/api/lumen/boards/{mac}/reboot")
    async def reboot(mac: str, request: Request) -> dict:
        return await fleet_of(request).reboot_device(mac_of(request, mac))

    @app.post("/api/lumen/backups/{backup_id}/restore")
    async def restore(backup_id: int, request: Request) -> dict:
        fleet = fleet_of(request)
        row = fleet.db.get_backup(backup_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Backup not found.")
        return await fleet.restore(row["mac"], backup_id)

    @app.websocket("/api/lumen/ws")
    async def live(websocket: WebSocket) -> None:
        settings = websocket.app.state.settings
        if settings.admin_password:
            ok = session_valid(
                websocket.cookies.get(COOKIE),
                websocket.app.state.session_secret,
                settings.admin_password,
            )
            if not ok:
                await websocket.close(code=4401)
                return
        await websocket.app.state.lumen.hub.serve(websocket)
