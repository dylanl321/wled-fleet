"""Lights, Zones, Looks and Favorites: the model the phone sees.

A Light is one WLED segment on one board. Boards stay plumbing: they are only
named in Maintenance and Find. Zones, names, Looks and Favorites live here on
the server. WLED keeps segments and current state.

Writes are optimistic. `apply` merges the change into the cached board state,
tells the phones, and hands the payload to that board's writer, which keeps at
most one request in flight and folds newer changes into the next one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Callable

from app.errors import FleetError, WledError
from app.sections import replay_state, restore_body
from app.starter_looks import STARTER_LOOKS
from app.util import utcnow

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS zones (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  collapsed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lights (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mac TEXT NOT NULL,
  seg INTEGER NOT NULL,
  name TEXT NOT NULL,
  zone_id INTEGER,
  position INTEGER NOT NULL DEFAULT 0,
  look_id INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE (mac, seg)
);

CREATE TABLE IF NOT EXISTS boards (
  mac TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS looks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  body_json TEXT NOT NULL,
  builtin INTEGER NOT NULL DEFAULT 0,
  uses INTEGER NOT NULL DEFAULT 0,
  last_used TEXT,
  position INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS favorites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  body_json TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
"""

LOOK_KEYS = ("fx", "fallback", "pal", "col", "sx", "ix")
QUIET_AFTER_WRITE = 1.2
HOLD_TIMEOUT = 90.0
DEFAULT_NAME = re.compile(r"^(wled|wled[-_ ]?[0-9a-f]{4,})$", re.IGNORECASE)


def _int(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _color(value: object) -> list[int] | None:
    if isinstance(value, str) and len(value) >= 6:
        try:
            return [int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)]
        except ValueError:
            return None
    if isinstance(value, list) and len(value) >= 3 and all(isinstance(item, int) and not isinstance(item, bool) for item in value[:3]):
        return [max(0, min(255, item)) for item in value[:3]]
    return None


def clean_look(body: object) -> dict:
    """A Look stores effect and palette by name."""
    if not isinstance(body, dict):
        raise FleetError("A look needs an effect.")
    look: dict = {}
    fx = body.get("fx")
    if not isinstance(fx, str) or not fx.strip():
        raise FleetError("A look needs an effect name.")
    look["fx"] = fx.strip()[:48]
    if isinstance(body.get("fallback"), str) and body["fallback"].strip():
        look["fallback"] = body["fallback"].strip()[:48]
    if isinstance(body.get("pal"), str) and body["pal"].strip():
        look["pal"] = body["pal"].strip()[:48]
    colors = []
    for item in body.get("col") or []:
        color = _color(item)
        if color is not None:
            colors.append(color)
    if colors:
        while len(colors) < 3:
            colors.append([0, 0, 0])
        look["col"] = colors[:3]
    for key in ("sx", "ix"):
        value = body.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            look[key] = max(0, min(255, value))
    return look


def merge_payload(base: dict | None, extra: dict) -> dict:
    """Fold a newer partial state into an unsent one. Newer values win, segments merge by id."""
    if not base:
        return json.loads(json.dumps(extra))
    merged = dict(base)
    for key, value in extra.items():
        if key != "seg":
            merged[key] = value
    if isinstance(extra.get("seg"), list):
        by_id: dict[int, dict] = {}
        order: list[int] = []
        for segment in (base.get("seg") or []) + extra["seg"]:
            if not isinstance(segment, dict):
                continue
            seg_id = _int(segment.get("id"))
            if seg_id not in by_id:
                by_id[seg_id] = {}
                order.append(seg_id)
            by_id[seg_id].update(segment)
        merged["seg"] = [by_id[seg_id] for seg_id in order]
    return merged


def merge_state(state: dict, payload: dict) -> dict:
    """What the board will look like once it accepts `payload`."""
    result = json.loads(json.dumps(state or {}))
    for key in ("on", "bri"):
        if key in payload:
            result[key] = payload[key]
    segments = result.setdefault("seg", [])
    for patch in payload.get("seg") or []:
        if not isinstance(patch, dict):
            continue
        seg_id = _int(patch.get("id"))
        target = next((item for item in segments if isinstance(item, dict) and _int(item.get("id")) == seg_id), None)
        if target is None:
            target = {"id": seg_id}
            segments.append(target)
        for key, value in patch.items():
            if key in {"i"}:
                continue
            target[key] = value
    return result


def segment_of(state: dict | None, seg_id: int) -> dict | None:
    for segment in (state or {}).get("seg") or []:
        if isinstance(segment, dict) and _int(segment.get("id")) == seg_id:
            return segment
    return None


def is_default_name(name: str | None) -> bool:
    return not name or bool(DEFAULT_NAME.match(name.strip()))


class Writer:
    """One board's outbound queue: one request in flight, newer changes merged into the next."""

    def __init__(self, lumen: "Lumen", mac: str):
        self.lumen = lumen
        self.mac = mac
        self.pending: dict | None = None
        self.failed: dict | None = None
        self.task: asyncio.Task | None = None
        self.last_sent = 0.0

    def gap(self) -> float:
        device = self.lumen.db.get_device(self.mac) or {}
        return 0.2 if str(device.get("arch") or "").startswith("esp8266") else 0.06

    def submit(self, payload: dict) -> None:
        self.pending = merge_payload(self.pending, payload)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while self.pending:
            wait = self.last_sent + self.gap() - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            body, self.pending = self.pending, None
            device = self.lumen.db.get_device(self.mac)
            if device is None:
                return
            self.lumen.quiet_until[self.mac] = time.monotonic() + QUIET_AFTER_WRITE
            self.last_sent = time.monotonic()
            try:
                await self.lumen.fleet.wled.post_state(device, {**body, "v": False})
                self.lumen.sent += 1
            except WledError as exc:
                log.info("write to %s failed: %s", device.get("name"), exc)
                # Keep the newest intent; it is replayed when the board answers again.
                self.failed = merge_payload(self.failed, body)
                self.lumen.notify({"t": "drift", "mac": self.mac, "error": str(exc)})

    def replay(self) -> bool:
        if not self.failed:
            return False
        body, self.failed = self.failed, None
        self.submit(body)
        return True


class Lumen:
    def __init__(self, fleet):
        self.fleet = fleet
        self.db = fleet.db
        with self.db._lock:
            self.db._conn.executescript(SCHEMA)
            self.db._conn.commit()
        self.states: dict[str, dict] = {}
        self.catalogs: dict[str, tuple[str, list[str], list[str]]] = {}
        self.writers: dict[str, Writer] = {}
        self.quiet_until: dict[str, float] = {}
        self.holds: dict[str, dict] = {}
        self.listeners: list[Callable[[dict], None]] = []
        self.sent = 0
        self._refresh_task: asyncio.Task | None = None
        self._seed()

    # ----- storage -------------------------------------------------------

    def _seed(self) -> None:
        if self.db.get_setting("lumen.seeded") == "1":
            return
        now = utcnow()
        for position, look in enumerate(STARTER_LOOKS):
            body = clean_look(look)
            self.db._run(
                "INSERT INTO looks (name, body_json, builtin, position, created_at) VALUES (?, ?, 1, ?, ?)",
                (look["name"], json.dumps(body), position, now),
            )
        self.db.set_setting("lumen.seeded", "1")

    def property_name(self) -> str:
        return self.db.get_setting("lumen.property") or "Home"

    def set_property_name(self, name: str) -> None:
        self.db.set_setting("lumen.property", name)

    def zones(self) -> list[dict]:
        rows = self.db._all("SELECT * FROM zones ORDER BY position, id")
        return [{"id": row["id"], "name": row["name"], "position": row["position"], "collapsed": bool(row["collapsed"])} for row in rows]

    def light_rows(self) -> list[dict]:
        return self.db._all("SELECT * FROM lights ORDER BY position, id")

    def light_row(self, light_id: int) -> dict:
        row = self.db._one("SELECT * FROM lights WHERE id = ?", (light_id,))
        if row is None:
            raise FleetError("Unknown light.", 404)
        return row

    def looks(self) -> list[dict]:
        rows = self.db._all("SELECT * FROM looks ORDER BY (last_used IS NULL), last_used DESC, uses DESC, position, id")
        return [
            {"id": row["id"], "name": row["name"], "body": json.loads(row["body_json"]), "builtin": bool(row["builtin"]), "uses": row["uses"]}
            for row in rows
        ]

    def look(self, look_id: int) -> dict:
        row = self.db._one("SELECT * FROM looks WHERE id = ?", (look_id,))
        if row is None:
            raise FleetError("Unknown look.", 404)
        return {"id": row["id"], "name": row["name"], "body": json.loads(row["body_json"])}

    def favorites(self) -> list[dict]:
        rows = self.db._all("SELECT * FROM favorites ORDER BY position, id")
        return [{"id": row["id"], "name": row["name"], "body": json.loads(row["body_json"])} for row in rows]

    def board_status(self, mac: str) -> str | None:
        row = self.db._one("SELECT status FROM boards WHERE mac = ?", (mac,))
        return row["status"] if row else None

    def set_board_status(self, mac: str, status: str) -> None:
        self.db._run(
            "INSERT INTO boards (mac, status, updated_at) VALUES (?, ?, ?) ON CONFLICT(mac) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
            (mac, status, utcnow()),
        )

    # ----- board state ---------------------------------------------------

    def devices(self) -> list[dict]:
        return [row for row in self.db.list_devices() if not row["forgotten"]]

    async def catalog(self, device: dict) -> tuple[list[str], list[str]]:
        mac = device["mac"]
        version = str(device.get("version") or "")
        cached = self.catalogs.get(mac)
        if cached and cached[0] == version and cached[1]:
            return cached[1], cached[2]
        effects: list[str] = []
        palettes: list[str] = []
        try:
            raw = await self.fleet.wled.get_json_any(device, "/json/eff", timeout=3.0)
            effects = [item for item in raw if isinstance(item, str)] if isinstance(raw, list) else []
            raw = await self.fleet.wled.get_json_any(device, "/json/pal", timeout=3.0)
            palettes = [item for item in raw if isinstance(item, str)] if isinstance(raw, list) else []
        except WledError:
            return (cached[1], cached[2]) if cached else ([], [])
        self.catalogs[mac] = (version, effects, palettes)
        return effects, palettes

    def accept_state(self, mac: str, state: dict, source: str = "board") -> None:
        """A state document from the board itself (HTTP or its WebSocket)."""
        if not isinstance(state, dict):
            return
        if source != "app" and time.monotonic() < self.quiet_until.get(mac, 0.0):
            # Our own write may not have landed yet; keep the intended state but take new geometry.
            known = self.states.get(mac)
            if known:
                self._take_geometry(known, state)
                return
        previous = self.states.get(mac)
        self.states[mac] = state
        self._adopt(mac, state)
        if previous != state:
            self.notify({"t": "lights", "lights": self.light_views(mac=mac), "source": source})

    @staticmethod
    def _take_geometry(known: dict, fresh: dict) -> None:
        for segment in fresh.get("seg") or []:
            if not isinstance(segment, dict):
                continue
            target = segment_of(known, _int(segment.get("id")))
            if target is None:
                known.setdefault("seg", []).append(dict(segment))
                continue
            for key in ("start", "stop", "len", "n"):
                if key in segment:
                    target[key] = segment[key]

    def _adopt(self, mac: str, state: dict) -> None:
        device = self.db.get_device(mac)
        if device is None:
            return
        status = self.board_status(mac)
        if status is None:
            if is_default_name(device.get("name")):
                self.set_board_status(mac, "new")
                return
            self.set_board_status(mac, "adopted")
            status = "adopted"
        if status != "adopted":
            return
        segments = [segment for segment in state.get("seg") or [] if isinstance(segment, dict) and _int(segment.get("stop")) > _int(segment.get("start"))]
        known = {row["seg"] for row in self.db._all("SELECT seg FROM lights WHERE mac = ?", (mac,))}
        position = (self.db._one("SELECT MAX(position) AS top FROM lights") or {}).get("top") or 0
        for index, segment in enumerate(segments):
            seg_id = _int(segment.get("id"), index)
            if seg_id in known:
                continue
            label = segment.get("n") if isinstance(segment.get("n"), str) and segment.get("n").strip() else None
            if not label:
                label = device["name"] if len(segments) == 1 else f"{device['name']} {index + 1}"
            position += 1
            self.db._run(
                "INSERT OR IGNORE INTO lights (mac, seg, name, position, created_at) VALUES (?, ?, ?, ?, ?)",
                (mac, seg_id, label[:40], position, utcnow()),
            )

    async def refresh_board(self, device: dict) -> bool:
        try:
            state = await self.fleet.wled.get_json(device, "/json/state", timeout=2.5)
        except WledError:
            return False
        await self.catalog(device)
        self.accept_state(device["mac"], state, source="poll")
        writer = self.writers.get(device["mac"])
        if writer and writer.replay():
            self.notify({"t": "applied", "mac": device["mac"]})
        return True

    async def refresh(self) -> None:
        devices = [row for row in self.devices() if row["online"]]
        await asyncio.gather(*(self.refresh_board(row) for row in devices), return_exceptions=True)

    def schedule_refresh(self) -> None:
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self.refresh())

    # ----- views ---------------------------------------------------------

    def light_view(self, row: dict, device: dict | None = None) -> dict | None:
        device = device or self.db.get_device(row["mac"])
        if device is None or device["forgotten"]:
            return None
        state = self.states.get(row["mac"])
        segment = segment_of(state, row["seg"])
        if state is not None and segment is None:
            return None
        catalog = self.catalogs.get(row["mac"])
        effects = catalog[1] if catalog else []
        palettes = catalog[2] if catalog else []
        view = {
            "id": row["id"],
            "name": row["name"],
            "zone_id": row["zone_id"],
            "position": row["position"],
            "look_id": row["look_id"],
            "mac": row["mac"],
            "seg": row["seg"],
            "online": bool(device["online"]),
            "chip": "esp8266" if str(device.get("arch") or "").startswith("esp8266") else "esp32",
            "known": segment is not None,
        }
        if segment is None:
            view.update({"on": False, "bri": 0, "fx": "Solid", "pal": "Default", "col": [[0, 0, 0]] * 3, "sx": 128, "ix": 128, "len": int(device.get("led_count") or 30)})
            return view
        master = _int(state.get("bri"), 128)
        level = round(master * _int(segment.get("bri"), 255) / 255)
        fx = _int(segment.get("fx"))
        pal = _int(segment.get("pal"))
        colors = [_color(item) or [0, 0, 0] for item in (segment.get("col") or [])][:3]
        while len(colors) < 3:
            colors.append([0, 0, 0])
        view.update(
            {
                "on": bool(state.get("on")) and segment.get("on", True) is not False,
                "bri": level,
                "fx": effects[fx] if 0 <= fx < len(effects) else ("Solid" if fx == 0 else f"Effect {fx}"),
                "pal": palettes[pal] if 0 <= pal < len(palettes) else "Default",
                "col": colors,
                "sx": _int(segment.get("sx"), 128),
                "ix": _int(segment.get("ix"), 128),
                "len": max(1, _int(segment.get("stop")) - _int(segment.get("start"))),
                "start": _int(segment.get("start")),
            }
        )
        return view

    def light_views(self, mac: str | None = None) -> list[dict]:
        devices = {row["mac"]: row for row in self.db.list_devices()}
        views = []
        for row in self.light_rows():
            if mac is not None and row["mac"] != mac:
                continue
            if self.board_status(row["mac"]) != "adopted":
                continue
            view = self.light_view(row, devices.get(row["mac"]))
            if view:
                views.append(view)
        return views

    def attention(self) -> dict:
        offline = 0
        new = 0
        for device in self.devices():
            status = self.board_status(device["mac"])
            if status == "adopted" and not device["online"]:
                offline += 1
            if status == "new" and device["online"]:
                new += 1
        return {"offline": offline, "new": new, "firmware": len(self.firmware_pending())}

    def firmware_pending(self) -> list[dict]:
        pending = []
        for device in self.devices():
            profile = self.db.get_profile(device.get("profile_id"))
            bin_id = device.get("bin_id") or (profile or {}).get("bin_id")
            if not bin_id:
                continue
            row = self.db.get_bin(bin_id)
            if row is None:
                continue
            match = re.search(r"(\d+\.\d+\.\d+(?:[.-][0-9a-z]+)?)", row["filename"], re.IGNORECASE)
            if match and device.get("version") and match.group(1) != device.get("version"):
                pending.append({"mac": device["mac"], "name": device["name"], "from": device.get("version"), "to": match.group(1), "bin_id": bin_id})
        return pending

    def home(self) -> dict:
        effects: list[str] = []
        palettes: list[str] = []
        for _version, eff, pal in self.catalogs.values():
            if len(eff) > len(effects):
                effects = eff
            if len(pal) > len(palettes):
                palettes = pal
        return {
            "property": self.property_name(),
            "zones": self.zones(),
            "lights": self.light_views(),
            "looks": self.looks(),
            "favorites": self.favorites(),
            "attention": self.attention(),
            "effects": effects,
            "palettes": palettes,
        }

    def notify(self, message: dict) -> None:
        for listener in list(self.listeners):
            try:
                listener(message)
            except Exception:
                log.exception("listener failed")

    # ----- writes --------------------------------------------------------

    async def resolve(self, device: dict, look: dict) -> tuple[dict, bool]:
        """Turn names into this board's ids. Returns the segment fields and whether the effect was missing."""
        effects, palettes = await self.catalog(device)
        fields: dict = {}
        missing = False
        if look.get("fx"):
            names = [name.lower() for name in effects]
            wanted = str(look["fx"]).lower()
            if wanted in names:
                fields["fx"] = names.index(wanted)
            elif look.get("fallback") and str(look["fallback"]).lower() in names:
                fields["fx"] = names.index(str(look["fallback"]).lower())
                missing = True
            else:
                fields["fx"] = 0
                missing = wanted != "solid"
        pal = look.get("pal") or "Default"
        lowered = [name.lower() for name in palettes]
        fields["pal"] = lowered.index(str(pal).lower()) if str(pal).lower() in lowered else 0
        if look.get("col"):
            fields["col"] = [list(color) for color in look["col"]]
        for key in ("sx", "ix"):
            if key in look:
                fields[key] = look[key]
        return fields, missing

    async def apply(self, changes: list[dict], *, preview: bool = False, transition: int | None = None) -> dict:
        """Apply per-light changes. Each change: {light, on?, bri?, look_id? | look?}."""
        if not changes:
            raise FleetError("Nothing to change.")
        tt = transition if transition is not None else (0 if preview else 4)
        by_board: dict[str, list[tuple[dict, dict]]] = {}
        look_ids: set[int] = set()
        for change in changes:
            row = self.light_row(int(change.get("light")))
            by_board.setdefault(row["mac"], []).append((row, change))
            if change.get("look_id") is not None:
                look_ids.add(int(change["look_id"]))
        looks = {look_id: self.look(look_id) for look_id in look_ids}
        missing: list[int] = []
        for mac, items in by_board.items():
            device = self.db.get_device(mac)
            if device is None:
                continue
            state = self.states.get(mac)
            if state is None and device["online"]:
                await self.refresh_board(device)
                state = self.states.get(mac)
            payload = await self._board_payload(device, state or {"on": False, "bri": 128, "seg": []}, items, looks, missing)
            payload["tt"] = tt
            self.states[mac] = merge_state(state or {}, payload)
            self.quiet_until[mac] = time.monotonic() + QUIET_AFTER_WRITE
            for row, change in items:
                if change.get("look_id") is not None and not preview:
                    self.db._run("UPDATE lights SET look_id = ? WHERE id = ?", (int(change["look_id"]), row["id"]))
                elif change.get("look") is not None and not preview:
                    self.db._run("UPDATE lights SET look_id = NULL WHERE id = ?", (row["id"],))
            self.writer(mac).submit(payload)
        if not preview:
            for look_id in look_ids:
                self.db._run("UPDATE looks SET uses = uses + 1, last_used = ? WHERE id = ?", (utcnow(), look_id))
        views = [view for mac in by_board for view in self.light_views(mac=mac)]
        self.notify({"t": "lights", "lights": views, "source": "app"})
        return {"lights": views, "missing": missing}

    def writer(self, mac: str) -> Writer:
        if mac not in self.writers:
            self.writers[mac] = Writer(self, mac)
        return self.writers[mac]

    async def _board_payload(self, device: dict, state: dict, items: list[tuple[dict, dict]], looks: dict, missing: list[int]) -> dict:
        master_on = bool(state.get("on"))
        master = _int(state.get("bri"), 128)
        segments = [segment for segment in state.get("seg") or [] if isinstance(segment, dict)]
        patches: dict[int, dict] = {}
        target_on: dict[int, bool] = {}
        want_bri: dict[int, int] = {}
        for row, change in items:
            seg_id = row["seg"]
            patch = patches.setdefault(seg_id, {"id": seg_id})
            look = None
            if change.get("look_id") is not None:
                look = looks[int(change["look_id"])]["body"]
            elif change.get("look") is not None:
                look = clean_look(change["look"])
            if look:
                fields, was_missing = await self.resolve(device, look)
                patch.update(fields)
                patch["frz"] = False
                if was_missing:
                    missing.append(row["id"])
                target_on.setdefault(seg_id, True)
            if change.get("on") is not None:
                target_on[seg_id] = bool(change["on"])
            if change.get("bri") is not None:
                level = max(0, min(255, int(change["bri"])))
                if level <= 0:
                    target_on[seg_id] = False
                else:
                    want_bri[seg_id] = level
                    if change.get("on") is None:
                        target_on.setdefault(seg_id, True)
        payload: dict = {}
        turning_on = any(target_on.values())
        if turning_on and not master_on:
            # Master was off: everything was dark. Keep the untouched siblings dark.
            payload["on"] = True
            for segment in segments:
                seg_id = _int(segment.get("id"))
                if seg_id not in target_on:
                    patches.setdefault(seg_id, {"id": seg_id})["on"] = False
            if master == 0:
                master = 255
                payload["bri"] = 255
        for seg_id, value in target_on.items():
            patches.setdefault(seg_id, {"id": seg_id})["on"] = value
        if want_bri:
            effective = master if master > 0 else 255
            if max(want_bri.values()) > effective:
                # Raise the board's master to full and scale siblings so they do not change.
                for segment in segments:
                    seg_id = _int(segment.get("id"))
                    if seg_id in want_bri:
                        continue
                    scaled = round(_int(segment.get("bri"), 255) * effective / 255)
                    patches.setdefault(seg_id, {"id": seg_id})["bri"] = max(1, scaled)
                payload["bri"] = 255
                effective = 255
            for seg_id, level in want_bri.items():
                patches.setdefault(seg_id, {"id": seg_id})["bri"] = max(1, min(255, round(level * 255 / effective)))
        if patches:
            payload["seg"] = list(patches.values())
        return payload

    # ----- edits ---------------------------------------------------------

    def rename_light(self, light_id: int, name: str) -> dict:
        self.light_row(light_id)
        self.db._run("UPDATE lights SET name = ? WHERE id = ?", (name, light_id))
        view = self.light_view(self.light_row(light_id))
        self.notify({"t": "lights", "lights": [view] if view else [], "source": "app"})
        return view or {}

    def create_zone(self, name: str, light_ids: list[int]) -> dict:
        top = (self.db._one("SELECT MAX(position) AS top FROM zones") or {}).get("top") or 0
        zone_id = self.db._run("INSERT INTO zones (name, position, created_at) VALUES (?, ?, ?)", (name, top + 1, utcnow()))
        for light_id in light_ids:
            self.light_row(light_id)
            self.db._run("UPDATE lights SET zone_id = ? WHERE id = ?", (zone_id, light_id))
        self.notify({"t": "layout"})
        return next(zone for zone in self.zones() if zone["id"] == zone_id)

    def update_zone(self, zone_id: int, name: str | None, collapsed: bool | None) -> dict:
        if self.db._one("SELECT id FROM zones WHERE id = ?", (zone_id,)) is None:
            raise FleetError("Unknown zone.", 404)
        if name is not None:
            self.db._run("UPDATE zones SET name = ? WHERE id = ?", (name, zone_id))
        if collapsed is not None:
            self.db._run("UPDATE zones SET collapsed = ? WHERE id = ?", (1 if collapsed else 0, zone_id))
        self.notify({"t": "layout"})
        return next(zone for zone in self.zones() if zone["id"] == zone_id)

    def delete_zone(self, zone_id: int) -> None:
        self.db._run("UPDATE lights SET zone_id = NULL WHERE zone_id = ?", (zone_id,))
        self.db._run("DELETE FROM zones WHERE id = ?", (zone_id,))
        self.notify({"t": "layout"})

    def set_layout(self, zones: list[int], lights: list[dict]) -> None:
        known = {zone["id"] for zone in self.zones()}
        for position, zone_id in enumerate(zones):
            if zone_id in known:
                self.db._run("UPDATE zones SET position = ? WHERE id = ?", (position, zone_id))
        for position, item in enumerate(lights):
            zone_id = item.get("zone_id")
            if zone_id is not None and zone_id not in known:
                zone_id = None
            self.db._run("UPDATE lights SET zone_id = ?, position = ? WHERE id = ?", (zone_id, position, int(item["id"])))
        # A zone emptied by a drag disappears, which is how zones are deleted by hand.
        for zone_id in known:
            if self.db._one("SELECT id FROM lights WHERE zone_id = ? LIMIT 1", (zone_id,)) is None:
                self.db._run("DELETE FROM zones WHERE id = ?", (zone_id,))
        self.notify({"t": "layout"})

    def save_look(self, name: str, body: dict) -> dict:
        look = clean_look(body)
        look_id = self.db._run(
            "INSERT INTO looks (name, body_json, builtin, position, created_at, last_used) VALUES (?, ?, 0, ?, ?, ?)",
            (name, json.dumps(look), 0, utcnow(), utcnow()),
        )
        self.notify({"t": "looks"})
        return self.look(look_id)

    def delete_look(self, look_id: int) -> None:
        self.look(look_id)
        self.db._run("DELETE FROM looks WHERE id = ?", (look_id,))
        self.db._run("UPDATE lights SET look_id = NULL WHERE look_id = ?", (look_id,))
        self.notify({"t": "looks"})

    def save_favorite(self, name: str, items: list[dict]) -> dict:
        clean = []
        for item in items:
            row = self.light_row(int(item["light"]))
            entry: dict = {"light": row["id"]}
            if item.get("on") is not None:
                entry["on"] = bool(item["on"])
            if item.get("bri") is not None:
                entry["bri"] = max(0, min(255, int(item["bri"])))
            if item.get("look") is not None:
                entry["look"] = clean_look(item["look"])
            clean.append(entry)
        if not clean:
            raise FleetError("A favorite needs at least one light.")
        top = (self.db._one("SELECT MAX(position) AS top FROM favorites") or {}).get("top") or 0
        favorite_id = self.db._run(
            "INSERT INTO favorites (name, body_json, position, created_at) VALUES (?, ?, ?, ?)",
            (name, json.dumps({"items": clean}), top + 1, utcnow()),
        )
        self.notify({"t": "favorites"})
        return next(item for item in self.favorites() if item["id"] == favorite_id)

    def delete_favorite(self, favorite_id: int) -> None:
        self.db._run("DELETE FROM favorites WHERE id = ?", (favorite_id,))
        self.notify({"t": "favorites"})

    async def apply_favorite(self, favorite_id: int) -> dict:
        favorite = next((item for item in self.favorites() if item["id"] == favorite_id), None)
        if favorite is None:
            raise FleetError("Unknown favorite.", 404)
        changes = []
        for item in favorite["body"]["items"]:
            if self.db._one("SELECT id FROM lights WHERE id = ?", (item["light"],)) is None:
                continue
            changes.append(item)
        return await self.apply(changes)

    # ----- setup and Find -----------------------------------------------

    def boards(self) -> list[dict]:
        rows = []
        for device in self.devices():
            state = self.states.get(device["mac"]) or {}
            lights = {row["seg"]: row for row in self.db._all("SELECT * FROM lights WHERE mac = ?", (device["mac"],))}
            outputs = []
            for index, segment in enumerate(state.get("seg") or []):
                if not isinstance(segment, dict):
                    continue
                seg_id = _int(segment.get("id"), index)
                light = lights.get(seg_id)
                outputs.append(
                    {
                        "seg": seg_id,
                        "start": _int(segment.get("start")),
                        "stop": _int(segment.get("stop")),
                        "light_id": light["id"] if light else None,
                        "name": light["name"] if light else (segment.get("n") or None),
                        "zone_id": light["zone_id"] if light else None,
                    }
                )
            rows.append(
                {
                    "mac": device["mac"],
                    "name": device["name"],
                    "ip": device["ip"],
                    "port": device["port"],
                    "version": device.get("version"),
                    "arch": device.get("arch"),
                    "online": bool(device["online"]),
                    "last_seen": device.get("last_seen"),
                    "status": self.board_status(device["mac"]) or "new",
                    "led_count": device.get("led_count"),
                    "outputs": outputs,
                    "held": device["mac"] in self.holds,
                }
            )
        return rows

    def setup_board(self, mac: str, outputs: list[dict]) -> dict:
        """Find names each output; the board's lights then appear on Home."""
        if self.db.get_device(mac) is None:
            raise FleetError("Unknown controller.", 404)
        position = (self.db._one("SELECT MAX(position) AS top FROM lights") or {}).get("top") or 0
        for item in outputs:
            seg_id = int(item["seg"])
            name = str(item.get("name") or "").strip()[:40]
            if not name:
                continue
            zone_id = item.get("zone_id")
            existing = self.db._one("SELECT id FROM lights WHERE mac = ? AND seg = ?", (mac, seg_id))
            if existing:
                self.db._run("UPDATE lights SET name = ?, zone_id = ? WHERE id = ?", (name, zone_id, existing["id"]))
            else:
                position += 1
                self.db._run(
                    "INSERT INTO lights (mac, seg, name, zone_id, position, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (mac, seg_id, name, zone_id, position, utcnow()),
                )
        self.set_board_status(mac, "adopted")
        self.notify({"t": "layout"})
        return {"ok": True}

    async def _hold(self, device: dict) -> dict:
        mac = device["mac"]
        hold = self.holds.get(mac)
        if hold:
            if hold.get("task"):
                hold["task"].cancel()
            hold["timer"].cancel()
            return hold
        state = await self.fleet.wled.get_json(device, "/json/state", timeout=3.0)
        hold = {"state": state, "task": None}
        self.holds[mac] = hold
        hold["timer"] = asyncio.create_task(self._expire(mac))
        return hold

    async def _expire(self, mac: str) -> None:
        try:
            await asyncio.sleep(HOLD_TIMEOUT)
        except asyncio.CancelledError:
            return
        await self.release(mac)

    async def release(self, mac: str) -> dict:
        hold = self.holds.pop(mac, None)
        if not hold:
            return {"restored": False}
        if hold.get("task"):
            hold["task"].cancel()
        timer = hold.get("timer")
        if timer and timer is not asyncio.current_task():
            timer.cancel()
        device = self.db.get_device(mac)
        if device is None:
            return {"restored": False}
        try:
            await self.fleet.wled.post_state(device, restore_body(hold["state"]))
        except WledError as exc:
            raise FleetError(str(exc)) from exc
        self.accept_state(mac, hold["state"], source="app")
        return {"restored": True}

    def _rearm(self, mac: str, hold: dict) -> None:
        hold["timer"] = asyncio.create_task(self._expire(mac))

    async def flash_board(self, mac: str) -> dict:
        """Strobe every output of this board white at 2 Hz until released."""
        device = self.fleet._require(mac)
        hold = await self._hold(device)
        self._rearm(mac, hold)
        segments = [segment for segment in hold["state"].get("seg") or [] if isinstance(segment, dict)]

        async def strobe() -> None:
            lit = True
            while True:
                color = [255, 255, 255] if lit else [0, 0, 0]
                body = {
                    "on": True,
                    "bri": 255,
                    "tt": 0,
                    "seg": [{"id": _int(item.get("id")), "on": True, "bri": 255, "fx": 0, "frz": False, "col": [color, [0, 0, 0], [0, 0, 0]]} for item in segments],
                }
                try:
                    await self.fleet.wled.post_state(device, body)
                except WledError:
                    pass
                lit = not lit
                await asyncio.sleep(0.25)

        hold["task"] = asyncio.create_task(strobe())
        return {"held": True}

    async def span(self, mac: str, seg_id: int | None, start: int, stop: int) -> dict:
        """Light LEDs [start, stop) of one output (relative to it) and darken the rest of the board."""
        device = self.fleet._require(mac)
        hold = await self._hold(device)
        self._rearm(mac, hold)
        body: dict = {"on": True, "bri": 255, "tt": 0, "seg": []}
        for segment in hold["state"].get("seg") or []:
            if not isinstance(segment, dict):
                continue
            current = _int(segment.get("id"))
            length = max(1, _int(segment.get("stop")) - _int(segment.get("start")))
            patch = {"id": current, "on": True, "bri": 255, "fx": 0, "frz": True, "col": [[0, 0, 0], [0, 0, 0], [0, 0, 0]]}
            if seg_id is None or current == seg_id:
                low = max(0, min(length, start))
                high = max(low, min(length, stop))
                patch["i"] = [0, length, "000000"] + ([low, high, "FFB000"] if high > low else [])
            else:
                patch["on"] = False
            body["seg"].append(patch)
        try:
            await self.fleet.wled.post_state(device, body)
        except WledError as exc:
            raise FleetError(str(exc)) from exc
        return {"held": True, "count": max(0, stop - start)}

    async def solo(self, mac: str, seg_id: int) -> dict:
        """Light only this output so you can see which physical strip it is."""
        device = self.fleet._require(mac)
        hold = await self._hold(device)
        self._rearm(mac, hold)
        segments = [segment for segment in hold["state"].get("seg") or [] if isinstance(segment, dict)]
        body = {
            "on": True,
            "bri": 255,
            "tt": 0,
            "seg": [
                {"id": _int(item.get("id")), "on": _int(item.get("id")) == seg_id, "bri": 255, "fx": 0, "frz": False, "col": [[255, 255, 255], [0, 0, 0], [0, 0, 0]]}
                for item in segments
            ],
        }
        try:
            await self.fleet.wled.post_state(device, body)
        except WledError as exc:
            raise FleetError(str(exc)) from exc
        return {"held": True}

    async def identify_light(self, light_id: int) -> dict:
        """Flash the real strip white three times, then put it back."""
        row = self.light_row(light_id)
        device = self.fleet._require(row["mac"])
        try:
            state = await self.fleet.wled.get_json(device, "/json/state", timeout=3.0)
        except WledError as exc:
            raise FleetError(str(exc)) from exc
        segment = segment_of(state, row["seg"]) or {"id": row["seg"]}
        saved = {key: segment[key] for key in ("on", "bri", "fx", "col", "frz") if key in segment}
        try:
            for _ in range(3):
                await self.fleet.wled.post_state(
                    device,
                    {"on": True, "tt": 0, "seg": [{"id": row["seg"], "on": True, "bri": 255, "fx": 0, "frz": False, "col": [[255, 255, 255], [0, 0, 0], [0, 0, 0]]}]},
                )
                await asyncio.sleep(0.2)
                await self.fleet.wled.post_state(device, {"tt": 0, "seg": [{"id": row["seg"], "on": False}]})
                await asyncio.sleep(0.2)
        finally:
            try:
                await self.fleet.wled.post_state(device, {**replay_state({"on": state.get("on"), "bri": state.get("bri")}), "seg": [{"id": row["seg"], **saved}]})
            except WledError:
                log.warning("could not restore %s after identify", device.get("name"))
        return {"ok": True}

    async def set_length(self, light_id: int, count: int) -> dict:
        row = self.light_row(light_id)
        return await self.set_segment_length(row["mac"], row["seg"], count)

    async def set_segment_length(self, mac: str, seg_id: int, count: int) -> dict:
        """Write the output's segment stop so it covers exactly `count` LEDs."""
        device = self.fleet._require(mac)
        hold = self.holds.get(mac)
        state = hold["state"] if hold else (self.states.get(mac) or await self.fleet.wled.get_json(device, "/json/state", timeout=3.0))
        segment = segment_of(state, seg_id)
        if segment is None:
            raise FleetError("That output is not on the board any more.")
        start = _int(segment.get("start"))
        stop = start + max(1, count)
        if hold:
            # Find is holding the board; change the state it will restore to.
            segment["stop"] = stop
            segment["len"] = stop - start
            return {"ok": True, "len": max(1, count)}
        body = {"seg": [{"id": seg_id, "start": start, "stop": stop}]}
        try:
            await self.fleet.wled.post_state(device, body)
        except WledError as exc:
            raise FleetError(str(exc)) from exc
        self.states[mac] = merge_state(state, body)
        self.notify({"t": "lights", "lights": self.light_views(mac=mac), "source": "app"})
        return {"ok": True, "len": max(1, count)}
