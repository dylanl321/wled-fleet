"""Fixture storage, DDP streaming, and ledmap upload."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from pathlib import Path

from app.discovery import normalize_mac
from app.errors import FleetError, WledError
from app.mapper.address import absolute_pixels, bus_start, migrate_saved_fixtures
from app.mapper.advertise import MapperAdvertiser
from app.mapper.compile import compile_fixture, pitch_mm_for_count, validate_counts, waypoint_index
from app.mapper.controllers import channels_for_order, list_controllers
from app.mapper.ddp import DDP_PORT, encode_pixels, send_frame
from app.mapper.effects import EFFECTS, effect_colors
from app.mapper.export import pixelblaze_map, pixels_csv, pixels_json, xlights_xmodel
from app.mapper.geom import normalise_points, vec3
from app.mapper.ledmap import grid_map, linear_by_height
from app.mapper.schema_check import SchemaError
from app.mapper.store import FixtureStore
from app.util import utcnow

log = logging.getLogger(__name__)

FRAME_SECONDS = 1.0 / 30.0


def backup_fleet_db(db, data_dir: Path) -> None:
    """Copy fleet.db once before the mapper runs against an existing volume.

    The mapper stores fixtures as JSON and does not change the fleet schema.
    The copy is there so a volume can be put back if a later migration does.
    """
    stamp_dir = data_dir / "migration-backups"
    stamp = stamp_dir / "mapper-v1.stamp"
    if stamp.exists():
        return
    stamp_dir.mkdir(parents=True, exist_ok=True)
    dest = stamp_dir / "fleet-before-mapper.db"
    if db.path.exists() and not dest.exists():
        db.backup_to(dest)
        log.info("Copied fleet database to %s", dest)
    stamp.write_text(utcnow() + "\n", encoding="utf-8")


class MapperService:
    def __init__(self, fleet, settings, ddp_port: int = DDP_PORT):
        self.fleet = fleet
        self.settings = settings
        self.ddp_port = ddp_port
        self.store = FixtureStore(settings.data_dir / "fixtures")
        self._sock: socket.socket | None = None
        self._sequences: dict[str, int] = {}
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._effect: dict | None = None
        self._highlight: dict | None = None
        self._token = 0
        self._subscribers: list[asyncio.Queue] = []
        self._frames = 0
        self._fps_mark = time.monotonic()
        self._advertiser = None
        self.status: dict = {
            "running": False,
            "effect": None,
            "fixtureId": None,
            "frame": 0,
            "fps": 0.0,
            "highlight": None,
            "error": None,
        }

    def start(self) -> None:
        backup_fleet_db(self.fleet.db, self.settings.data_dir)
        migrate_saved_fixtures(self.store, self.fleet, self.settings.data_dir)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if self.settings.scheduler_enabled:
            self._advertiser = MapperAdvertiser()
            self._advertiser.start(self.settings.port)
        self._task = asyncio.create_task(self._loop(), name="wled-mapper")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        if self._advertiser is not None:
            self._advertiser.close()
            self._advertiser = None

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def controllers(self) -> list[dict]:
        return list_controllers(self.fleet)

    def list_fixtures(self) -> list[dict]:
        return self.store.list_documents()

    def get_fixture(self, fixture_id: str) -> dict:
        document = self._require(fixture_id)
        return document

    def save_fixture(self, document: dict) -> dict:
        try:
            return self.store.save(document)
        except SchemaError as exc:
            raise FleetError(str(exc), 400) from exc

    def delete_fixture(self, fixture_id: str) -> None:
        if not self.store.delete(fixture_id):
            raise FleetError("Unknown fixture.", 404)

    def pixelmap(self, fixture_id: str) -> dict:
        fixture = self._require(fixture_id)
        pixels = compile_fixture(fixture)
        return {
            "fixtureId": fixture_id,
            "pixels": [pixel.as_dict() for pixel in pixels],
            "runs": validate_counts(fixture, pixels),
        }

    def normalise(self, fixture_id: str) -> dict:
        fixture = self._require(fixture_id)
        waypoints = fixture.get("waypoints") if isinstance(fixture.get("waypoints"), list) else []
        raw = [vec3(item.get("position")) for item in waypoints if isinstance(item, dict)]
        _moved, frame = normalise_points(raw)
        updated = json.loads(json.dumps(fixture))
        for waypoint, point in zip(
            [item for item in updated["waypoints"] if isinstance(item, dict)],
            _moved,
        ):
            waypoint["position"] = point.as_list()
        updated["frame"] = frame.as_dict()
        return self.save_fixture(updated)

    def scale_pitch(self, fixture_id: str, run_id: str) -> dict:
        fixture = self._require(fixture_id)
        waypoints = waypoint_index(fixture)
        updated = json.loads(json.dumps(fixture))
        found = False
        for run in updated.get("runs") or []:
            if not isinstance(run, dict) or run.get("id") != run_id:
                continue
            count = int(run.get("pixelCount") or 0)
            run["pitchMm"] = pitch_mm_for_count(run, waypoints, count)
            found = True
        if not found:
            raise FleetError("Unknown run.", 404)
        return self.save_fixture(updated)

    async def highlight(self, fixture_id: str, controller_id: str, output: int, index: int, hold: float = 2.5) -> dict:
        self._require(fixture_id)
        mac = self._mac(controller_id)
        if index < 0 or index > 20_000:
            raise FleetError("Pixel index is out of range.", 400)
        async with self._lock:
            self._token += 1
            self._highlight = {
                "fixtureId": fixture_id,
                "controller": mac,
                "output": int(output),
                "index": int(index),
                "until": time.monotonic() + max(0.2, min(hold, 30.0)),
            }
            self._effect = None
        self._wake.set()
        await self._publish_status(running=False, effect=None, fixture_id=fixture_id, highlight=self._highlight_view())
        return {"ok": True, "controller": mac, "output": int(output), "index": int(index)}

    async def start_effect(self, fixture_id: str, name: str, params: dict | None) -> dict:
        self._require(fixture_id)
        if name not in EFFECTS:
            raise FleetError(f"Unknown effect {name}.", 400)
        async with self._lock:
            self._token += 1
            self._highlight = None
            self._effect = {
                "fixtureId": fixture_id,
                "name": name,
                "params": params or {},
                "started": time.monotonic(),
            }
        self._wake.set()
        await self._publish_status(running=True, effect=name, fixture_id=fixture_id, highlight=None)
        return {"ok": True, "effect": name, "fixtureId": fixture_id}

    async def stop_effect(self, fixture_id: str) -> dict:
        fixture = self._require(fixture_id)
        async with self._lock:
            self._token += 1
            self._effect = None
            self._highlight = None
        await self._send_fixture(fixture, black=True)
        await self._publish_status(running=False, effect=None, fixture_id=fixture_id, highlight=None)
        return {"ok": True}

    async def push_ledmap(self, fixture_id: str, body: dict) -> dict:
        if body.get("confirm") is not True:
            raise FleetError(
                "Uploading a ledmap reboots the controller. Ask first, then send confirm: true.",
                400,
            )
        slot = body.get("slot", 1)
        if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= 9:
            raise FleetError("Ledmap slot must be from 1 to 9.", 400)
        mode = body.get("mode") or "height"
        fixture = self._require(fixture_id)
        pixels = absolute_pixels(compile_fixture(fixture), self.controllers())
        controllers = []
        for pixel in pixels:
            if pixel.controller not in controllers:
                controllers.append(pixel.controller)
        if len(controllers) != 1:
            raise FleetError(
                "A ledmap covers one controller. This fixture spans more than one, so run a spatial effect instead.",
                400,
            )
        mac = self._mac(controllers[0])
        device = self.fleet.db.get_device(mac)
        if device is None:
            raise FleetError("That controller is not in the fleet.", 404)
        if mode == "height":
            document = linear_by_height(pixels)
        elif mode == "grid":
            width = body.get("width")
            height = body.get("height")
            if isinstance(width, bool) or not isinstance(width, int) or isinstance(height, bool) or not isinstance(height, int):
                raise FleetError("A 2D ledmap needs integer width and height.", 400)
            try:
                document = grid_map(
                    pixels,
                    width=width,
                    height=height,
                    surface_hint=str(fixture.get("surfaceHint") or "freeform"),
                )
            except ValueError as exc:
                raise FleetError(str(exc), 400) from exc
        else:
            raise FleetError("Ledmap mode must be height or grid.", 400)
        payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
        filename = f"ledmap{slot}.json"
        try:
            await self.fleet.wled.put_file(device, filename, payload)
            info = await self.fleet.wled.reboot(device)
            saved_bytes = await self.fleet.wled.get_bytes(device, f"/{filename}")
            saved = json.loads(saved_bytes)
        except WledError as exc:
            raise FleetError(str(exc), exc.status) from exc
        except (json.JSONDecodeError, OSError) as exc:
            raise FleetError(f"Controller rebooted but the ledmap could not be read back. {exc}", 502) from exc
        if saved.get("map") != document["map"]:
            raise FleetError("Controller came back but the ledmap on it does not match.", 502)
        leds = info.get("leds") if isinstance(info.get("leds"), dict) else {}
        return {
            "ok": True,
            "slot": slot,
            "file": filename,
            "mode": mode,
            "confirmed": True,
            "info": {
                "name": info.get("name"),
                "version": info.get("ver"),
                "leds": leds.get("count") if isinstance(leds, dict) else None,
            },
        }

    def export(self, fixture_id: str, fmt: str, width: int, height: int, depth: int) -> tuple[str, str, str]:
        fixture = self._require(fixture_id)
        compiled = compile_fixture(fixture)
        pixels = absolute_pixels(compiled, self.controllers()) if fmt == "xmodel" else compiled
        filename = _safe_stem(str(fixture.get("name") or fixture_id))
        if fmt == "csv":
            return pixels_csv(pixels), "text/csv", f"{filename}.csv"
        if fmt == "json":
            return pixels_json(fixture_id, pixels), "application/json", f"{filename}.json"
        if fmt == "pixelblaze":
            return pixelblaze_map(pixels), "application/json", f"{filename}.pixelblaze.json"
        if fmt == "xmodel":
            controller = pixels[0].controller if pixels else ""
            body = xlights_xmodel(
                str(fixture.get("name") or "Fixture"),
                pixels,
                surface_hint=str(fixture.get("surfaceHint") or "freeform"),
                width=width,
                height=height,
                depth=depth,
                controller_name=controller,
            )
            return body, "application/xml", f"{filename}.xmodel"
        raise FleetError("Export format must be csv, json, pixelblaze, or xmodel.", 400)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            job = await self._job()
            if job is None:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                continue
            started = time.monotonic()
            try:
                await self._render(job)
            except Exception:
                log.warning("mapper frame failed", exc_info=True)
                await self._note_error("A frame did not reach a controller.")
            remaining = FRAME_SECONDS - (time.monotonic() - started)
            if remaining > 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    pass

    async def _job(self) -> tuple[str, dict] | None:
        async with self._lock:
            if self._effect is not None:
                return "effect", dict(self._effect)
            highlight = self._highlight
            if highlight is not None and highlight["until"] > time.monotonic():
                return "highlight", dict(highlight)
            if highlight is not None:
                self._highlight = None
            return None

    async def _render(self, job: tuple[str, dict]) -> None:
        kind, payload = job
        token = self._token
        if kind == "highlight":
            await self._send_highlight(payload, token)
        else:
            fixture = self.store.get(payload["fixtureId"])
            if fixture is None or token != self._token:
                return
            seconds = _seconds(payload.get("params") or {})
            phase = ((time.monotonic() - payload["started"]) / seconds) % 1.0
            await self._send_fixture(fixture, effect=payload["name"], phase=phase, token=token)
        self._frames += 1
        now = time.monotonic()
        elapsed = now - self._fps_mark
        fps = self.status["fps"]
        if elapsed >= 1.0:
            fps = self._frames / elapsed
            self._frames = 0
            self._fps_mark = now
        await self._publish_status(
            running=kind == "effect",
            effect=payload.get("name"),
            fixture_id=payload.get("fixtureId"),
            highlight=self._highlight_view() if kind == "highlight" else None,
            fps=fps,
        )

    async def _send_highlight(self, payload: dict, token: int) -> None:
        mac = payload["controller"]
        device = self.fleet.db.get_device(mac)
        if device is None:
            return
        order = _order_for(self.controllers(), mac, int(payload["output"]))
        channels = channels_for_order(order)
        origin = bus_start(self.controllers(), mac, int(payload["output"])) or 0
        colors = {origin + int(payload["index"]): (255, 255, 255)}
        led_count = _output_count(self.controllers(), mac)
        if led_count:
            for index in range(led_count):
                colors.setdefault(index, (0, 0, 0))
        await self._send(mac, str(device["ip"]), encode_pixels(colors, channels), channels, token)

    async def _send_fixture(
        self,
        fixture: dict,
        effect: str | None = None,
        phase: float = 0.0,
        black: bool = False,
        token: int | None = None,
    ) -> None:
        compiled = compile_fixture(fixture)
        if not compiled:
            return
        if token is not None and token != self._token:
            return
        pixels = absolute_pixels(compiled, self.controllers())
        colors = [(0, 0, 0)] * len(pixels) if black or effect is None else effect_colors(compiled, effect, phase)
        grouped: dict[str, dict[int, tuple[int, int, int]]] = {}
        orders: dict[str, int] = {}
        for pixel, color in zip(pixels, colors):
            grouped.setdefault(pixel.controller, {})[pixel.index] = color
            if str(_run_order(fixture, pixel.controller)).endswith("W"):
                orders[pixel.controller] = 4
            else:
                orders.setdefault(pixel.controller, 3)
        for controller, color_map in grouped.items():
            mac = normalize_mac(controller) or controller
            device = self.fleet.db.get_device(mac)
            if device is None:
                continue
            channels = orders.get(controller, 3)
            await self._send(mac, str(device["ip"]), encode_pixels(color_map, channels), channels, token)

    async def _send(self, mac: str, ip: str, payload: bytes, channels: int, token: int | None = None) -> None:
        if self._sock is None or not payload:
            return
        if token is not None and token != self._token:
            return
        sequence = self._sequences.get(mac, 1)
        try:
            sequence = send_frame(self._sock, (ip, self.ddp_port), payload, sequence, channels)
        except OSError as exc:
            await self._note_error(f"Could not reach {ip}: {exc.__class__.__name__}")
            return
        self._sequences[mac] = sequence

    def _require(self, fixture_id: str) -> dict:
        try:
            document = self.store.get(fixture_id)
        except SchemaError as exc:
            raise FleetError(str(exc), 400) from exc
        if document is None:
            raise FleetError("Unknown fixture.", 404)
        return document

    def _mac(self, controller_id: str) -> str:
        mac = normalize_mac(controller_id)
        if mac is None or self.fleet.db.get_device(mac) is None:
            raise FleetError("Unknown controller.", 404)
        return mac

    def _highlight_view(self) -> dict | None:
        highlight = self._highlight
        if highlight is None:
            return None
        return {
            "controller": highlight["controller"],
            "output": highlight["output"],
            "index": highlight["index"],
        }

    async def _publish_status(
        self,
        *,
        running: bool,
        effect: str | None,
        fixture_id: str | None,
        highlight: dict | None,
        fps: float | None = None,
    ) -> None:
        self.status = {
            "running": running,
            "effect": effect,
            "fixtureId": fixture_id,
            "frame": self.status["frame"] + 1,
            "fps": self.status["fps"] if fps is None else round(fps, 1),
            "highlight": highlight,
            "error": None,
        }
        self._fanout()

    async def _note_error(self, message: str) -> None:
        self.status = dict(self.status)
        self.status["error"] = message
        self._fanout()

    def _fanout(self) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(dict(self.status))
            except asyncio.QueueFull:
                pass


def _seconds(params: dict) -> float:
    raw = params.get("seconds", 6)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 6.0
    return min(max(value, 0.5), 120.0)


def _order_for(controllers: list[dict], mac: str, output: int) -> str:
    for controller in controllers:
        if controller["id"] != mac:
            continue
        for item in controller["outputs"]:
            if item["index"] == output:
                return str(item["colorOrder"])
    return "GRB"


def _output_count(controllers: list[dict], mac: str) -> int:
    for controller in controllers:
        if controller["id"] != mac:
            continue
        if not controller["outputs"]:
            return 0
        return max(item["start"] + item["pixelCount"] for item in controller["outputs"])
    return 0


def _run_order(fixture: dict, controller: str) -> str:
    for run in fixture.get("runs") or []:
        if isinstance(run, dict) and run.get("controllerId") == controller:
            return str(run.get("colorOrder") or "GRB")
    return "GRB"


def _safe_stem(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_" else "-" for char in name).strip("-")
    return cleaned[:60] or "fixture"

