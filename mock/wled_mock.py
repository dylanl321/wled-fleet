from __future__ import annotations

import asyncio
import colorsys
import json
import logging
import math
import os
import re
import socket
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse
from zeroconf import IPVersion, ServiceInfo, Zeroconf

log = logging.getLogger("wled-mock")

SKIP_CFG_KEYS = {"pin", "v", "rb", "time", "tt"}

# The first eight indexes stay put: older tests paint by number.
EFFECTS = [
    "Solid",
    "Blink",
    "Breathe",
    "Wipe",
    "Rainbow",
    "Chase",
    "Theater",
    "Scanner",
    "Candle",
    "Fire Flicker",
    "Colorwaves",
    "Pacifica",
    "Lake",
    "Rain",
    "Twinkle",
    "Colorloop",
    "Halloween",
    "Merry Christmas",
    "Twinklefox",
    "Sparkle",
    "Heartbeat",
    "Fireworks 1D",
    "Candy Cane",
    "Sunset",
    "Noise 1",
    "Meteor",
    "Scan",
]
PALETTES = ["Default", "Warm", "Ocean", "Rainbow", "Party", "Forest", "Fire", "Aurora", "Icefire", "Red & Blue", "Sunset", "Candy"]


@dataclass
class MockSpec:
    mac: str
    name: str
    mdns: str
    arch: str
    release: str
    version: str
    leds: int
    peers: list[str] = field(default_factory=list)
    pin: str = ""
    ota_passphrase: str = ""
    ota_lock: bool = False
    reboot_seconds: float = 0.6
    rssi: int = -58
    buses: tuple[tuple[int, int, int], ...] = ()
    max_sockets: int | None = None


class MockDevice:
    def __init__(self, spec: MockSpec):
        self.spec = spec
        self.version = spec.version
        self.ota_lock = spec.ota_lock
        self.pin_ok = not spec.pin
        self.down_until = 0.0
        self.started = time.time()
        self.writes: list[dict] = []
        self.clients: list[WebSocket] = []
        self.live_client: WebSocket | None = None
        self.max_sockets = spec.max_sockets if spec.max_sockets is not None else (2 if "8266" in spec.arch else 4)
        self.cfg = {
            "rev": [1, 0],
            "id": {"mdns": spec.mdns, "name": spec.name, "inv": spec.name},
            "nw": {"ins": [{"ssid": "House", "pskl": 11, "ip": [0, 0, 0, 0]}]},
            "ap": {"ssid": "WLED-AP", "pskl": 8},
            "hw": {"led": {"total": spec.leds, "maxpwr": 850, "ins": []}},
            "ota": {"lock": spec.ota_lock, "pskl": len(spec.ota_passphrase)},
        }
        self.presets = {
            "1": {"n": "Warm evening", "on": True, "bri": 140, "seg": [{"id": 0, "col": [[255, 150, 60]]}]},
            "2": {"n": "Night", "on": True, "bri": 18, "seg": [{"id": 0, "col": [[255, 80, 20]]}]},
            "3": {"n": "Reading", "on": True, "bri": 200, "seg": [{"id": 0, "col": [[255, 220, 180]]}]},
        }
        self.state = {
            "on": True,
            "bri": 128,
            "ps": -1,
            "transition": 7,
            "seg": [],
            "udpn": {"send": False, "recv": True},
        }
        self._install_buses()
        self._lock = threading.Lock()

    def bus_tuples(self) -> list[tuple[int, int, int]]:
        if self.spec.buses:
            return [(int(start), int(length), int(pin)) for start, length, pin in self.spec.buses]
        pin = 16 if self.spec.arch.startswith("esp32") else 2
        return [(0, int(self.spec.leds), pin)]

    def _install_buses(self) -> None:
        buses = self.bus_tuples()
        ins = []
        segments = []
        total = 0
        for index, (start, length, pin) in enumerate(buses):
            ins.append(
                {
                    "start": start,
                    "len": length,
                    "pin": [pin],
                    "order": 0,
                    "rev": False,
                    "skip": 0,
                    "type": 22,
                }
            )
            segments.append(
                {
                    "id": index,
                    "start": start,
                    "stop": start + length,
                    "len": length,
                    "on": True,
                    "bri": 255,
                    "rev": False,
                    "col": [[255, 160, 60], [40, 0, 0], [0, 0, 0]],
                    "fx": 0,
                    "pal": 0,
                    "sx": 128,
                    "ix": 128,
                }
            )
            total += length
        self.cfg["hw"]["led"]["ins"] = ins
        self.cfg["hw"]["led"]["total"] = total or int(self.spec.leds)
        self.state["seg"] = segments or self.state["seg"]

    def strip_length(self) -> int:
        total = 0
        for segment in self.state.get("seg") or []:
            if isinstance(segment, dict):
                total = max(total, int(segment.get("stop") or 0))
        return total or int(self.cfg["hw"]["led"].get("total") or self.spec.leds)

    def reboot(self) -> None:
        self.down_until = time.monotonic() + self.spec.reboot_seconds

    def rebooting(self) -> bool:
        return time.monotonic() < self.down_until

    def info(self) -> dict:
        total = int(self.cfg["hw"]["led"]["total"])
        return {
            "ver": self.version,
            "vid": 1600010,
            "leds": {"count": total, "lc": total, "pwr": 120, "fps": 42, "maxpwr": 850, "maxseg": 16},
            "name": self.cfg["id"]["name"],
            "udpport": 21324,
            "arch": self.spec.arch,
            "core": "mock",
            "brand": "WLED",
            "product": "FOSS",
            "rel": self.spec.release,
            "mac": self.spec.mac.replace(":", ""),
            "wifi": {"rssi": self.spec.rssi, "signal": 70, "channel": 6},
            "uptime": int(time.time() - self.started),
        }

    def authorized(self, payload: dict | None) -> bool:
        if not self.spec.pin:
            return True
        if payload and str(payload.get("pin") or "") == self.spec.pin:
            self.pin_ok = True
            return True
        return self.pin_ok

    def apply_state(self, payload: dict) -> None:
        self.writes.append({"t": time.monotonic(), "body": json.loads(json.dumps(payload))})
        if payload.get("rb"):
            self.reboot()
            return
        if "ps" in payload:
            self.state["ps"] = payload["ps"]
            preset = self.presets.get(str(payload["ps"]))
            if isinstance(preset, dict):
                for key in ("on", "bri"):
                    if key in preset:
                        self.state[key] = preset[key]
                if isinstance(preset.get("seg"), list):
                    self._merge_segments(preset["seg"])
        for key in ("on", "bri"):
            if key in payload:
                self.state[key] = payload[key]
        if isinstance(payload.get("udpn"), dict):
            current = self.state.setdefault("udpn", {"send": False, "recv": True})
            if isinstance(current, dict):
                current.update({key: bool(value) for key, value in payload["udpn"].items() if key in {"send", "recv"}})
        segments = payload.get("seg")
        if isinstance(segments, list):
            self._merge_segments(segments)

    def _merge_segments(self, segments: list) -> None:
        for patch in segments:
            if not isinstance(patch, dict):
                continue
            target = None
            for segment in self.state["seg"]:
                if segment.get("id") == patch.get("id", 0):
                    target = segment
                    break
            if target is None:
                target = {"id": patch.get("id", 0)}
                self.state["seg"].append(target)
            for key, value in patch.items():
                if key != "id":
                    target[key] = value

    def live_colors(self) -> list[list[int]]:
        count = max(12, min(36, int(self.spec.leds / 4) or 12))
        return render_live(self.state, count, time.time())

    def live_bytes(self) -> bytes:
        colors = render_strip(self.state, self.strip_length(), time.time())
        if "8266" in self.spec.arch and len(colors) > 256:
            step = max(1, len(colors) // 256)
            colors = colors[::step]
        body = bytearray((ord("L"), 1))
        for color in colors:
            body.extend(int(channel) & 255 for channel in color[:3])
        return bytes(body)

    def apply_cfg(self, payload: dict) -> None:
        ota = payload.get("ota") if isinstance(payload.get("ota"), dict) else None
        if ota and ota.get("psk") and ota.get("psk") == self.spec.ota_passphrase:
            self.ota_lock = bool(ota.get("lock", False))
            self.cfg.setdefault("ota", {})["lock"] = self.ota_lock
        deep_merge(self.cfg, {key: value for key, value in payload.items() if key not in SKIP_CFG_KEYS})
        if "hw" in payload:
            self.sync_length()
        if payload.get("rb"):
            self.reboot()

    def sync_length(self) -> None:
        ins = self.cfg.get("hw", {}).get("led", {}).get("ins") or []
        total = 0
        for item in ins:
            if isinstance(item, dict):
                total += int(item.get("len") or 0)
        if total <= 0:
            total = int(self.cfg["hw"]["led"].get("total") or 1)
        self.cfg["hw"]["led"]["total"] = total
        if len(self.state["seg"]) == 1:
            self.state["seg"][0]["stop"] = total
            self.state["seg"][0]["len"] = total
        elif not self.state["seg"]:
            self._install_buses()

    def store_upload(self, filename: str, raw: bytes) -> str:
        name = filename.rsplit("/", 1)[-1]
        if name == "cfg.json":
            self.cfg = json.loads(raw)
            self.sync_length()
            self.reboot()
            return "Configuration restore successful.\nRebooting..."
        if name == "presets.json":
            self.presets = json.loads(raw)
            return "File Uploaded!"
        if name == "wsec.json":
            raise ValueError("refusing wsec.json")
        return "File Uploaded!"

    def store_firmware(self, filename: str, raw: bytes) -> None:
        if self.ota_lock:
            raise PermissionError("Please unlock OTA in security settings!")
        if not raw.startswith(b"\xe9"):
            raise ValueError("bad image")
        found = version_from_filename(filename)
        if found:
            self.version = found
        self.reboot()


def render_live(state: dict, count: int, now: float) -> list[list[int]]:
    """Downsampled LED colors for /json/live. Effects move with time and speed."""
    if count <= 0:
        return []
    segments = state.get("seg") if isinstance(state.get("seg"), list) else []
    length = count
    for segment in segments:
        if isinstance(segment, dict) and isinstance(segment.get("stop"), int):
            length = max(length, int(segment["stop"]))
    full = render_strip(state, max(length, 1), now)
    if len(full) == count:
        return full
    step = len(full) / count
    return [full[min(len(full) - 1, int(index * step))] for index in range(count)]


def render_strip(state: dict, length: int, now: float) -> list[list[int]]:
    """One RGB triple per LED, each segment painted on its own range."""
    length = max(1, int(length))
    buffer = [[0, 0, 0] for _ in range(length)]
    segments = state.get("seg") if isinstance(state.get("seg"), list) else []
    if not segments:
        segments = [{}]
    master = int(state.get("bri") or 0) if state.get("on", True) else 0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        if "start" in segment or "stop" in segment:
            start = int(segment.get("start") or 0)
            stop = int(segment.get("stop") or length)
        else:
            start, stop = 0, length
        start = max(0, min(length, start))
        stop = max(start, min(length, stop))
        if stop <= start:
            continue
        colors = segment.get("col") if isinstance(segment.get("col"), list) else []
        primary = colors[0] if colors and isinstance(colors[0], list) else [255, 160, 60]
        secondary = colors[1] if len(colors) > 1 and isinstance(colors[1], list) else [0, 0, 0]
        primary = [int(primary[i]) if i < len(primary) else 0 for i in range(3)]
        secondary = [int(secondary[i]) if i < len(secondary) else 0 for i in range(3)]
        fx = int(segment.get("fx") or 0)
        speed = int(segment.get("sx") or 128)
        seg_on = segment.get("on") is not False
        seg_bri = int(segment.get("bri") or 255)
        level = int(master * seg_bri / 255) if seg_on else 0
        clock = now * (0.35 + speed / 160)
        width = stop - start
        for index in range(width):
            phase = index / width
            if fx == 1:
                mix = 1.0 if int(clock * 2) % 2 == 0 else 0.08
                rgb = [int(channel * mix) for channel in primary]
            elif fx == 2:
                mix = 0.25 + 0.75 * (0.5 + 0.5 * _wave(clock * 2))
                rgb = [int(channel * mix) for channel in primary]
            elif fx == 3:
                head = (clock * 0.35) % 1
                mix = max(0.0, 1 - abs(phase - head) * width / 3)
                rgb = [int(primary[channel] * mix + secondary[channel] * (1 - mix)) for channel in range(3)]
            elif fx == 4:
                rgb = _hue(phase + clock * 0.2)
            elif fx in {5, 7}:
                head = int(clock * 4) % width
                rgb = primary if index == head else secondary
            elif fx == 6:
                rgb = primary if (index + int(clock * 3)) % 3 else secondary
            elif fx == 0:
                rgb = list(primary)
            else:
                flicker = 0.55 + 0.45 * abs(_wave(clock * (1 + (fx % 5)) + phase * 6))
                if fx % 2:
                    rgb = _hue(phase * 0.2 + clock * 0.12)
                else:
                    rgb = [int(channel * flicker) for channel in primary]
            buffer[start + index] = [max(0, min(255, int(channel * level / 255))) for channel in rgb]
    return buffer


def _wave(value: float) -> float:
    return math.sin(value)


def _hue(value: float) -> list[int]:
    red, green, blue = colorsys.hsv_to_rgb(value % 1, 0.85, 1)
    return [int(red * 255), int(green * 255), int(blue * 255)]


def version_from_filename(name: str | None) -> str | None:
    if not name:
        return None
    match = re.search(r"WLED[_-]v?(\d+(?:\.\d+){1,3})", name, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def deep_merge(destination: dict, source: dict) -> None:
    for key, value in source.items():
        if key in SKIP_CFG_KEYS:
            continue
        if isinstance(value, dict) and isinstance(destination.get(key), dict):
            deep_merge(destination[key], value)
        else:
            destination[key] = value


async def _pump(device: MockDevice) -> None:
    while True:
        await asyncio.sleep(0.05)
        client = device.live_client
        if client is None:
            continue
        try:
            await client.send_bytes(device.live_bytes())
        except Exception:
            if device.live_client is client:
                device.live_client = None


def build_app(device: MockDevice) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(_pump(device))
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def reboot_gate(request: Request, call_next):
        if device.rebooting():
            return PlainTextResponse("rebooting", status_code=503)
        return await call_next(request)

    @app.get("/json/info")
    async def info() -> dict:
        return device.info()

    @app.get("/json/eff")
    async def effects() -> list[str]:
        return EFFECTS

    @app.get("/json/pal")
    async def palettes() -> list[str]:
        return PALETTES

    @app.get("/json/live")
    async def live() -> dict:
        colors = device.live_colors()
        return {"leds": colors, "n": len(colors)}

    @app.get("/json/state")
    async def get_state() -> dict:
        return device.state

    @app.post("/json/state")
    async def post_state(request: Request):
        payload = await _json_body(request)
        if not device.authorized(payload):
            return JSONResponse({"error": "denied"}, status_code=401)
        device.apply_state(payload)
        if payload.get("v"):
            return {"success": True, "state": device.state}
        return {"success": True}

    @app.post("/json")
    async def post_json(request: Request):
        return await post_state(request)

    @app.get("/json/cfg")
    async def get_cfg() -> dict:
        return device.cfg

    @app.post("/json/cfg")
    async def post_cfg(request: Request):
        payload = await _json_body(request)
        if not device.authorized(payload):
            return JSONResponse({"error": "denied"}, status_code=401)
        device.apply_cfg(payload)
        return {"success": True, "id": device.cfg.get("id")}

    @app.get("/cfg.json")
    async def cfg_file() -> JSONResponse:
        return JSONResponse(device.cfg)

    @app.get("/presets.json")
    async def presets_file() -> JSONResponse:
        return JSONResponse(device.presets)

    @app.get("/json/nodes")
    async def nodes() -> dict:
        return {
            "nodes": [
                {"name": peer, "type": 32, "ip": peer, "age": 1, "vid": 1600010}
                for peer in device.spec.peers
            ]
        }

    @app.post("/upload")
    async def upload(request: Request):
        if not device.authorized({}):
            return PlainTextResponse("Please unlock settings using PIN code!", status_code=401)
        form = await request.form()
        for item in form.values():
            filename = getattr(item, "filename", None)
            if not filename:
                continue
            raw = await item.read()
            try:
                message = device.store_upload(filename, raw)
            except json.JSONDecodeError:
                return PlainTextResponse("invalid json", status_code=400)
            except ValueError as exc:
                return PlainTextResponse(str(exc), status_code=403)
            return PlainTextResponse(message)
        return PlainTextResponse("missing file", status_code=400)

    @app.post("/update")
    async def update(request: Request):
        form = await request.form()
        for item in form.values():
            if not hasattr(item, "read"):
                continue
            raw = await item.read()
            try:
                device.store_firmware(getattr(item, "filename", None), raw)
            except PermissionError as exc:
                return PlainTextResponse(str(exc), status_code=401)
            except ValueError:
                return PlainTextResponse("Update failed!", status_code=500)
            return PlainTextResponse("Update successful!\nRebooting...")
        return PlainTextResponse("missing file", status_code=400)

    @app.get("/debug/ws")
    async def debug_ws() -> dict:
        return {
            "clients": len(device.clients),
            "max": device.max_sockets,
            "live": device.live_client is not None,
            "writes": device.writes[-80:],
        }

    @app.websocket("/ws")
    async def socket(ws: WebSocket) -> None:
        await ws.accept()
        if len(device.clients) >= device.max_sockets:
            await ws.close(code=1008)
            return
        device.clients.append(ws)
        try:
            await ws.send_json({"state": device.state, "info": device.info()})
            while True:
                message = await ws.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                text = message.get("text")
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if "lv" in payload and set(payload) <= {"lv", "v"}:
                    if payload.get("lv"):
                        device.live_client = ws
                    elif device.live_client is ws:
                        device.live_client = None
                    continue
                if payload.get("v") and len(payload) == 1:
                    await ws.send_json({"state": device.state})
                    continue
                device.apply_state(payload)
                outgoing = json.dumps({"success": True, "state": device.state})
                for client in list(device.clients):
                    try:
                        await client.send_text(outgoing)
                    except Exception:
                        if client in device.clients:
                            device.clients.remove(client)
        finally:
            if device.live_client is ws:
                device.live_client = None
            if ws in device.clients:
                device.clients.remove(ws)

    return app


async def _json_body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def spec_from_env() -> MockSpec:
    peers = [item.strip() for item in os.environ.get("MOCK_PEERS", "").split(",") if item.strip()]
    return MockSpec(
        mac=os.environ.get("MOCK_MAC", "a1b2c3d4e5f0"),
        name=os.environ.get("MOCK_NAME", "Dig-Uno Shelf"),
        mdns=os.environ.get("MOCK_MDNS_NAME", "dig-uno-shelf"),
        arch=os.environ.get("MOCK_ARCH", "esp32"),
        release=os.environ.get("MOCK_RELEASE", "Dig-Uno-V3"),
        version=os.environ.get("MOCK_VERSION", "16.0.1"),
        leds=int(os.environ.get("MOCK_LEDS", "150")),
        peers=peers,
        pin=os.environ.get("MOCK_PIN", ""),
        ota_passphrase=os.environ.get("MOCK_OTA_PASSPHRASE", ""),
        ota_lock=os.environ.get("MOCK_OTA_LOCK", "").lower() in {"1", "true", "yes"},
        reboot_seconds=float(os.environ.get("MOCK_REBOOT_SECONDS", "0.8")),
        rssi=int(os.environ.get("MOCK_RSSI", "-58")),
    )


def advertise(spec: MockSpec, port: int):
    try:
        address = local_ipv4()
        properties = {b"mac": spec.mac.replace(":", "").encode()}
        zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        infos = [
            ServiceInfo(
                "_wled._tcp.local.",
                f"{spec.mdns}._wled._tcp.local.",
                addresses=[socket.inet_aton(address)],
                port=port,
                properties=properties,
                server=f"{spec.mdns}.local.",
            ),
            ServiceInfo(
                "_http._tcp.local.",
                f"{spec.mdns}._http._tcp.local.",
                addresses=[socket.inet_aton(address)],
                port=port,
                properties=properties,
                server=f"{spec.mdns}.local.",
            ),
        ]
        for info in infos:
            zeroconf.register_service(info)
        log.info("advertising %s on %s:%s", spec.name, address, port)
        return zeroconf
    except Exception:
        log.warning("mDNS advertisement failed", exc_info=True)
        return None


def local_ipv4() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    spec = spec_from_env()
    device = MockDevice(spec)
    app = build_app(device)
    host = os.environ.get("MOCK_HOST", "0.0.0.0")
    port = int(os.environ.get("MOCK_PORT", "9091"))
    browser = None
    if os.environ.get("MOCK_MDNS", "0").lower() in {"1", "true", "yes"}:
        browser = advertise(spec, port)
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        if browser is not None:
            browser.close()


if __name__ == "__main__":
    main()
