"""The server is the one live-view client of every board and fans frames out to phones.

WLED streams pixel colors over its WebSocket after {"lv":true}, but only to one
client at a time, and each socket costs a small board RAM. So phones never talk to
boards: each phone holds one socket to this hub, which sends every Light's slice of
its board's latest frame (10 fps for ribbons on screen, 1 fps for the rest) and the
light state changes. Phones send their writes back over the same socket, so a
preview can never overtake the commit that followed it.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import math
import time

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from app.errors import FleetError, WledError

log = logging.getLogger(__name__)

TICK = 0.1
SLOW_EVERY = 10
MAX_POINTS = 120
LINGER = 30.0
STALL = 3.0


def parse_ws_frame(data: bytes) -> list[list[int]] | None:
    """WLED live frame: b'L', version, [w, h for version 2], then RGB triples."""
    if len(data) < 2 or data[0] != ord("L"):
        return None
    offset = 4 if data[1] == 2 else 2
    body = data[offset:]
    return [[body[i], body[i + 1], body[i + 2]] for i in range(0, len(body) - 2, 3)]


def parse_http_live(payload: object) -> tuple[list[list[int]], int]:
    """/json/live: {"leds": ["RRGGBB", ...], "n": step}. Older shapes use numbers."""
    step = 1
    raw = payload
    if isinstance(payload, dict):
        raw = payload.get("leds")
        if isinstance(payload.get("n"), int) and payload["n"] > 0:
            step = payload["n"]
    colors: list[list[int]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and len(item) >= 6:
                try:
                    hexa = item[-6:]
                    colors.append([int(hexa[0:2], 16), int(hexa[2:4], 16), int(hexa[4:6], 16)])
                except ValueError:
                    continue
            elif isinstance(item, list) and len(item) >= 3:
                colors.append([int(item[0]) & 255, int(item[1]) & 255, int(item[2]) & 255])
            elif isinstance(item, int):
                colors.append([(item >> 16) & 255, (item >> 8) & 255, item & 255])
    return colors, step


def _linear(channel: int) -> float:
    return (channel / 255) ** 2.2


def _encode(channel: float) -> int:
    return max(0, min(255, round((channel ** (1 / 2.2)) * 255)))


def slice_frame(frame: list[list[int]], step: int, start: int, stop: int, points: int = MAX_POINTS) -> bytes:
    """One Light's pixels, averaged in linear light when the strip is longer than the ribbon needs."""
    low = max(0, start // max(1, step))
    high = max(low + 1, math.ceil(stop / max(1, step)))
    pixels = frame[low:high]
    if not pixels:
        return b""
    if len(pixels) <= points:
        return bytes(channel for color in pixels for channel in color)
    out = bytearray()
    per = len(pixels) / points
    for index in range(points):
        chunk = pixels[int(index * per) : max(int(index * per) + 1, int((index + 1) * per))]
        for channel in range(3):
            out.append(_encode(sum(_linear(color[channel]) for color in chunk) / len(chunk)))
    return bytes(out)


class BoardLink:
    def __init__(self, hub: "Hub", mac: str):
        self.hub = hub
        self.mac = mac
        self.task = asyncio.create_task(self.run(), name=f"live-{mac}")
        self.socket = None
        self.mode = "connecting"

    def stop(self) -> None:
        self.task.cancel()

    def device(self) -> dict | None:
        return self.hub.fleet.db.get_device(self.mac)

    async def run(self) -> None:
        while True:
            device = self.device()
            if device is None:
                return
            try:
                await self.stream(device)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("live socket for %s: %s", device.get("name"), exc)
            # The socket failed or is not there; poll for a while, then try the socket again.
            self.mode = "poll"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                device = self.device()
                if device is None:
                    return
                await self.poll_once(device)
                await asyncio.sleep(0.2 if str(device.get("arch") or "").startswith("esp8266") else 0.1)

    async def stream(self, device: dict) -> None:
        url = f"ws://{device['ip']}:{device['port']}/ws"
        async with websockets.connect(url, open_timeout=3, close_timeout=1, max_size=2**20, ping_interval=20) as socket:
            self.socket = socket
            self.mode = "socket"
            await socket.send(json.dumps({"lv": True}))
            last_frame = time.monotonic()
            try:
                while True:
                    try:
                        message = await asyncio.wait_for(socket.recv(), timeout=STALL)
                    except asyncio.TimeoutError:
                        message = None
                    if isinstance(message, bytes):
                        frame = parse_ws_frame(message)
                        if frame:
                            self.hub.put_frame(self.mac, frame, None)
                            last_frame = time.monotonic()
                    elif isinstance(message, str):
                        with contextlib.suppress(ValueError):
                            data = json.loads(message)
                            if isinstance(data, dict) and isinstance(data.get("state"), dict):
                                self.hub.lumen.accept_state(self.mac, data["state"])
                    if time.monotonic() - last_frame > STALL:
                        # Someone else (WLED's own Peek) took the stream. Ask again.
                        await socket.send(json.dumps({"lv": True}))
                        last_frame = time.monotonic()
            finally:
                self.socket = None
                with contextlib.suppress(Exception):
                    await socket.send(json.dumps({"lv": False}))

    async def poll_once(self, device: dict) -> None:
        try:
            payload = await self.hub.fleet.wled.get_json_any(device, "/json/live", timeout=1.5)
        except WledError:
            await asyncio.sleep(1.0)
            return
        frame, step = parse_http_live(payload)
        if frame:
            self.hub.put_frame(self.mac, frame, step)
        if time.monotonic() - self.hub.state_polled.get(self.mac, 0) > 3:
            self.hub.state_polled[self.mac] = time.monotonic()
            with contextlib.suppress(WledError):
                state = await self.hub.fleet.wled.get_json(device, "/json/state", timeout=2.0)
                self.hub.lumen.accept_state(self.mac, state)


class Client:
    def __init__(self, socket: WebSocket):
        self.socket = socket
        self.visible: set[int] = set()
        self.queue: asyncio.Queue = asyncio.Queue()
        self.sent: dict[int, bytes] = {}
        self.ticks = 0


class Hub:
    def __init__(self, fleet, lumen):
        self.fleet = fleet
        self.lumen = lumen
        self.clients: set[Client] = set()
        self.links: dict[str, BoardLink] = {}
        self.frames: dict[str, tuple[list[list[int]], int, float]] = {}
        self.state_polled: dict[str, float] = {}
        self.idle_since = time.monotonic()
        self._supervisor: asyncio.Task | None = None
        self._last_digest = ""
        self._views: list[dict] = []
        self._views_at = 0.0
        lumen.listeners.append(self.on_change)

    # ----- frames ---------------------------------------------------------

    def put_frame(self, mac: str, frame: list[list[int]], step: int | None) -> None:
        if step is None:
            device = self.fleet.db.get_device(mac) or {}
            total = int(device.get("led_count") or len(frame))
            step = max(1, round(total / max(1, len(frame))))
        self.frames[mac] = (frame, step, time.monotonic())

    def light_frame(self, light: dict) -> bytes | None:
        stored = self.frames.get(light["mac"])
        if not stored or time.monotonic() - stored[2] > 5:
            return None
        frame, step, _at = stored
        start = int(light.get("start") or 0)
        return slice_frame(frame, step, start, start + int(light.get("len") or 1))

    def board_frame(self, mac: str) -> bytes | None:
        stored = self.frames.get(mac)
        if not stored or time.monotonic() - stored[2] > 5:
            return None
        frame, step, _at = stored
        return slice_frame(frame, 1, 0, len(frame), points=240)

    # ----- clients --------------------------------------------------------

    def on_change(self, message: dict) -> None:
        kind = message.get("t")
        self._views_at = 0.0
        if kind in {"layout", "looks", "favorites"} or (kind == "home" and "home" not in message):
            message = {"t": "home", "home": self.lumen.home()}
        for client in list(self.clients):
            client.queue.put_nowait(message)

    async def serve(self, socket: WebSocket) -> None:
        await socket.accept()
        client = Client(socket)
        self.clients.add(client)
        self.ensure_supervisor()
        self.sync_links()
        await socket.send_json({"t": "home", "home": self.lumen.home()})
        self.lumen.schedule_refresh()
        sender = asyncio.create_task(self.send_loop(client))
        try:
            while True:
                message = await socket.receive_json()
                if isinstance(message, dict):
                    await self.handle(client, message)
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception:
            log.exception("stream client failed")
        finally:
            sender.cancel()
            self.clients.discard(client)
            if not self.clients:
                self.idle_since = time.monotonic()

    async def handle(self, client: Client, message: dict) -> None:
        kind = message.get("t")
        if kind == "vis":
            client.visible = {int(item) for item in message.get("ids") or [] if isinstance(item, int)}
            client.board = message.get("board")
            return
        if kind == "ping":
            client.queue.put_nowait({"t": "pong"})
            return
        if kind == "apply":
            seq = message.get("seq")
            try:
                result = await self.lumen.apply(
                    list(message.get("changes") or []),
                    preview=bool(message.get("preview")),
                    transition=message.get("tt") if isinstance(message.get("tt"), int) else None,
                )
                client.queue.put_nowait({"t": "ack", "seq": seq, "missing": result["missing"]})
            except (FleetError, ValueError, KeyError, TypeError) as exc:
                client.queue.put_nowait({"t": "error", "seq": seq, "detail": str(exc)})

    async def send_loop(self, client: Client) -> None:
        try:
            while True:
                started = time.monotonic()
                while not client.queue.empty():
                    await client.socket.send_json(client.queue.get_nowait())
                client.ticks += 1
                frames = self.frames_for(client)
                if frames:
                    await client.socket.send_json({"t": "f", "f": frames})
                board = getattr(client, "board", None)
                if board:
                    data = self.board_frame(board)
                    if data is not None:
                        await client.socket.send_json({"t": "bf", "mac": board, "f": base64.b64encode(data).decode()})
                await asyncio.sleep(max(0.0, TICK - (time.monotonic() - started)))
        except asyncio.CancelledError:
            raise
        except (WebSocketDisconnect, RuntimeError, websockets.ConnectionClosed):
            return
        except Exception:
            log.exception("stream sender failed")
            return

    def frames_for(self, client: Client) -> dict[str, str]:
        slow = client.ticks % SLOW_EVERY == 0
        out: dict[str, str] = {}
        for light in self.views():
            if light["id"] not in client.visible and not slow:
                continue
            data = self.light_frame(light)
            if data is None or client.sent.get(light["id"]) == data:
                continue
            client.sent[light["id"]] = data
            out[str(light["id"])] = base64.b64encode(data).decode()
        return out

    def views(self) -> list[dict]:
        if time.monotonic() - self._views_at > 0.5:
            self._views = self.lumen.light_views()
            self._views_at = time.monotonic()
        return self._views

    # ----- board links ----------------------------------------------------

    def ensure_supervisor(self) -> None:
        if self._supervisor is None or self._supervisor.done():
            self._supervisor = asyncio.create_task(self.supervise())

    async def supervise(self) -> None:
        while True:
            await asyncio.sleep(2)
            if not self.clients and time.monotonic() - self.idle_since > LINGER:
                for link in self.links.values():
                    link.stop()
                self.links.clear()
                return
            self.sync_links()
            home = self.lumen.home()
            digest = json.dumps([home["attention"], [(light["id"], light["online"]) for light in home["lights"]]])
            if digest != self._last_digest:
                self._last_digest = digest
                self.on_change({"t": "home"})

    def sync_links(self) -> None:
        wanted = {row["mac"] for row in self.lumen.devices() if row["online"]}
        for mac in list(self.links):
            if mac not in wanted:
                self.links.pop(mac).stop()
        for mac in wanted:
            if mac not in self.links or self.links[mac].task.done():
                self.links[mac] = BoardLink(self, mac)

    def stop(self) -> None:
        for link in self.links.values():
            link.stop()
        self.links.clear()
        if self._supervisor:
            self._supervisor.cancel()
