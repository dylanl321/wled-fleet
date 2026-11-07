"""One live-view socket per board, fanned out to phones on a single socket.

WLED allows one peek client at a time and only two WebSocket connections on
ESP8266. The phone never opens a board socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlparse

import websockets

from app.domain import (
    MAX_WIRE_LEDS,
    PHONE_TICK_S,
    BatchWriter,
    WriteSequencer,
    board_profile,
    downsample_linear,
    parse_live_binary,
    should_send,
    slice_frame,
)
from app.util import base_url

log = logging.getLogger(__name__)


def _ws_url(device: dict) -> str:
    parsed = urlparse(base_url(str(device["ip"]), int(device["port"])))
    return f"ws://{parsed.netloc}/ws"


@dataclass(eq=False)
class Phone:
    queue: asyncio.Queue
    visible: set[int] = field(default_factory=set)
    sent_tick: dict[int, int] = field(default_factory=dict)
    closed: bool = False


class BoardLink:
    """The only socket this process opens to one controller."""

    def __init__(self, hub: LiveHub, mac: str, arch: str | None):
        self.hub = hub
        self.mac = mac
        self.profile = board_profile(arch)
        self.sequencer = WriteSequencer()
        self.batch = BatchWriter(self.profile.write_gap)
        self.effects: list[str] = []
        self.palettes: list[str] = []
        self.connected = False
        self.sockets = 0
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._pending: tuple[int, dict] | None = None
        self._sent: deque[int] = deque()
        self.offline_commit: dict | None = None
        self._ws = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"wled-{self.mac}")

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def submit(self, kind: str, body: dict) -> dict:
        decision = self.sequencer.submit(kind, body)
        if decision["send"] is not None:
            self._pending = (decision["seq"], decision["send"])
            self._wake.set()
        return decision

    async def _run(self) -> None:
        delay = 0.4
        while not self._stop.is_set():
            try:
                await self._session()
                delay = 0.4
            except asyncio.CancelledError:
                raise
            except Exception:
                log.info("live link to %s dropped", self.mac, exc_info=False)
            self.connected = False
            if self._stop.is_set():
                break
            await asyncio.sleep(delay)
            delay = min(delay * 1.6, 8.0)

    async def _session(self) -> None:
        device = self.hub.device(self.mac)
        if device is None:
            await asyncio.sleep(1.0)
            return
        # Exactly one socket. ESP8266 is never given a second.
        self.sockets = 1
        async with websockets.connect(
            _ws_url(device),
            open_timeout=4,
            max_size=2**20,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            self._ws = ws
            self.connected = True
            await ws.send(json.dumps({"lv": True}))
            if self.offline_commit is not None and self._pending is None:
                self._pending = (self.sequencer.latest, self.offline_commit)
            self.offline_commit = None
            await self.hub.note_connected(self)
            reader = asyncio.create_task(self._read(ws))
            writer = asyncio.create_task(self._write(ws))
            try:
                done, pending = await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                for task in done:
                    task.result()
            finally:
                self._ws = None
                self.connected = False

    async def _read(self, ws) -> None:
        last_frame = 0.0
        interval = 1 / max(self.profile.live_fps, 1)
        async for message in ws:
            if self._stop.is_set():
                return
            if isinstance(message, bytes):
                now = time.monotonic()
                if now - last_frame < interval:
                    continue
                last_frame = now
                colors = parse_live_binary(message)
                if colors:
                    self.hub.on_frame(self.mac, colors)
                continue
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                continue
            state = payload.get("state") if isinstance(payload, dict) else None
            if isinstance(state, dict):
                seq = self._sent.popleft() if self._sent else None
                if seq is not None and not self.sequencer.accepts(seq):
                    continue
                self.hub.on_state(self.mac, state)

    async def _write(self, ws) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            if self._pending is None:
                await self._wake.wait()
                continue
            if self.profile.write_gap:
                await asyncio.sleep(self.profile.write_gap)
            pending = self._pending
            self._pending = None
            if pending is None:
                continue
            seq, body = pending
            # A newer body may have replaced this one during the gap.
            if self._pending is not None:
                seq, body = self._pending
                self._pending = None
            sent = self.batch.push(time.monotonic(), seq, body)
            if not sent:
                self._pending = (seq, body)
                await asyncio.sleep(self.profile.write_gap or 0.02)
                continue
            seq, body = sent[-1]
            outgoing = dict(body)
            outgoing.setdefault("v", True)
            self._sent.append(seq)
            await ws.send(json.dumps(outgoing))
            follow = self.sequencer.finished(seq)
            if follow and follow.get("send") is not None:
                self._pending = (follow["seq"], follow["send"])
                self._wake.set()


class LiveHub:
    def __init__(self, lumen):
        self.lumen = lumen
        self.links: dict[str, BoardLink] = {}
        self.phones: set[Phone] = set()
        self.frames: dict[int, dict] = {}
        self._frame_seq = 0
        self._stop = asyncio.Event()
        self._fanout: asyncio.Task | None = None

    def device(self, mac: str) -> dict | None:
        return self.lumen.fleet.db.get_device(mac)

    def start(self) -> None:
        if self._fanout is None:
            self._fanout = asyncio.create_task(self._fan(), name="lumen-fanout")

    def stop(self) -> None:
        self._stop.set()
        for link in self.links.values():
            link.stop()
        for phone in list(self.phones):
            phone.closed = True
            with contextlib.suppress(asyncio.QueueFull):
                phone.queue.put_nowait(None)

    def ensure(self, mac: str, arch: str | None) -> BoardLink:
        link = self.links.get(mac)
        if link is None:
            link = BoardLink(self, mac, arch)
            self.links[mac] = link
            link.start()
        return link

    def submit(self, mac: str, arch: str | None, kind: str, body: dict) -> dict:
        if not body:
            return {"seq": 0, "send": None, "dropped": []}
        return self.ensure(mac, arch).submit(kind, body)

    async def note_connected(self, link: BoardLink) -> None:
        await self.lumen.refresh_catalog(link)

    def on_frame(self, mac: str, colors: list[list[int]]) -> None:
        lights = self.lumen.lights_for_mac(mac)
        if not lights:
            return
        strip_len = max(int(light["stop"]) for light in lights)
        now = time.time()
        for light in lights:
            sliced = slice_frame(colors, int(light["start"]), int(light["stop"]), strip_len)
            wired = downsample_linear(sliced, min(MAX_WIRE_LEDS, max(len(sliced), 1)))
            self._frame_seq += 1
            self.frames[int(light["id"])] = {"leds": wired, "seq": self._frame_seq, "at": now, "mac": mac}

    def on_state(self, mac: str, state: dict) -> None:
        self.lumen.ingest_state(mac, state)

    async def serve(self, websocket) -> None:
        await websocket.accept()
        phone = Phone(queue=asyncio.Queue(maxsize=4))
        self.phones.add(phone)
        sender = asyncio.create_task(self._send(websocket, phone))
        try:
            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "visible":
                    visible: set[int] = set()
                    for item in message.get("ids") or []:
                        if isinstance(item, bool):
                            continue
                        if isinstance(item, int):
                            visible.add(item)
                        elif isinstance(item, str) and item.isdigit():
                            visible.add(int(item))
                    phone.visible = visible
        except Exception:
            pass
        finally:
            phone.closed = True
            self.phones.discard(phone)
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender

    async def _send(self, websocket, phone: Phone) -> None:
        while not phone.closed and not self._stop.is_set():
            try:
                payload = await asyncio.wait_for(phone.queue.get(), timeout=30)
            except asyncio.TimeoutError:
                payload = {"type": "ping"}
            if payload is None or phone.closed:
                return
            try:
                await websocket.send_json(payload)
            except Exception:
                phone.closed = True
                return

    async def _fan(self) -> None:
        tick = 0
        while not self._stop.is_set():
            await asyncio.sleep(PHONE_TICK_S)
            tick += 1
            if not self.phones or not self.frames:
                continue
            profiles = {mac: link.profile.live_fps for mac, link in self.links.items()}
            for phone in list(self.phones):
                if phone.closed:
                    continue
                items = []
                for light_id, frame in self.frames.items():
                    visible = light_id in phone.visible
                    last = phone.sent_tick.get(light_id, -1)
                    fps = profiles.get(frame["mac"], 10)
                    if not should_send(visible, tick, last, fps):
                        continue
                    phone.sent_tick[light_id] = tick
                    items.append({"id": light_id, "leds": frame["leds"], "t": frame["at"], "seq": frame["seq"]})
                if not items:
                    continue
                payload = {"type": "frames", "items": items}
                if phone.queue.full():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        phone.queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    phone.queue.put_nowait(payload)

    def publish_state(self, lights: list[dict]) -> None:
        if not lights or not self.phones:
            return
        payload = {"type": "state", "lights": lights}
        for phone in list(self.phones):
            if phone.closed:
                continue
            if phone.queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    phone.queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                phone.queue.put_nowait(payload)
