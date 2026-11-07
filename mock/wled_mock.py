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
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse
from zeroconf import IPVersion, ServiceInfo, Zeroconf

log = logging.getLogger("wled-mock")

SKIP_CFG_KEYS = {"pin", "v", "rb", "time", "tt"}

from mock.render import EFFECTS, PALETTES, render_state


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
    # Named runs on separate outputs, e.g. [("Porch rail", 40), ("Porch ceiling", 20)].
    outputs: list[tuple[str, int]] = field(default_factory=list)


class MockDevice:
    def __init__(self, spec: MockSpec):
        self.spec = spec
        self.version = spec.version
        self.ota_lock = spec.ota_lock
        self.pin_ok = not spec.pin
        self.down_until = 0.0
        self.started = time.time()
        runs = spec.outputs or [("", spec.leds)]
        buses = []
        segments = []
        cursor = 0
        palette = [[255, 160, 60], [255, 120, 40], [255, 200, 140], [255, 90, 30]]
        for index, (label, length) in enumerate(runs):
            buses.append(
                {
                    "start": cursor,
                    "len": length,
                    "pin": [(16 if spec.arch.startswith("esp32") else 2) + index],
                    "order": 0,
                    "rev": False,
                    "skip": 0,
                    "type": 22,
                }
            )
            segment = {
                "id": index,
                "start": cursor,
                "stop": cursor + length,
                "len": length,
                "on": True,
                "bri": 255,
                "rev": False,
                "col": [palette[index % len(palette)], [40, 0, 0], [0, 0, 0]],
                "fx": 0,
                "pal": 0,
                "sx": 128,
                "ix": 128,
            }
            if label:
                segment["n"] = label
            segments.append(segment)
            cursor += length
        self.cfg = {
            "rev": [1, 0],
            "id": {"mdns": spec.mdns, "name": spec.name, "inv": spec.name},
            "nw": {"ins": [{"ssid": "House", "pskl": 11, "ip": [0, 0, 0, 0]}]},
            "ap": {"ssid": "WLED-AP", "pskl": 8},
            "hw": {
                "led": {
                    "total": cursor,
                    "maxpwr": 850,
                    "ins": buses,
                }
            },
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
            "seg": segments,
            "udpn": {"send": False, "recv": True},
        }
        self._lock = threading.Lock()
        self.sockets: list = []
        self.live_socket = None

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

    def total(self) -> int:
        return int(self.cfg["hw"]["led"].get("total") or self.spec.leds)

    def pixels(self) -> list[list[int]]:
        return render_state(self.state, self.total(), time.time())

    def live_colors(self) -> list[list[int]]:
        """What /json/live returns: every LED, or every n-th LED past the build's limit."""
        pixels = self.pixels()
        limit = 1024 if self.spec.arch.startswith("esp32") else 256
        step = max(1, -(-len(pixels) // limit))
        return pixels[::step]

    def max_sockets(self) -> int:
        return 8 if self.spec.arch.startswith("esp32") else 3

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
    """Colors for a strip of `count` LEDs, one segment spanning all of it unless given."""
    segments = state.get("seg") if isinstance(state.get("seg"), list) else []
    patched = dict(state)
    patched["seg"] = [
        {**segment, "start": segment.get("start", 0), "stop": segment.get("stop", count)}
        for segment in segments
        if isinstance(segment, dict)
    ]
    return render_state(patched, count, now)


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


def build_app(device: MockDevice) -> FastAPI:
    app = FastAPI()

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
        step = max(1, -(-device.total() // max(1, len(colors))))
        return {"leds": ["%02X%02X%02X" % tuple(color) for color in colors], "n": step}

    async def broadcast() -> None:
        message = {"state": device.state, "info": device.info()}
        for socket in list(device.sockets):
            try:
                await socket.send_json(message)
            except Exception:
                if socket in device.sockets:
                    device.sockets.remove(socket)

    async def stream_live(socket: WebSocket) -> None:
        interval = 0.04 if device.spec.arch.startswith("esp32") else 0.08
        while device.live_socket is socket:
            colors = device.live_colors()
            frame = bytearray(b"L\x01")
            for color in colors:
                frame.extend(bytes(color))
            try:
                await socket.send_bytes(bytes(frame))
            except Exception:
                return
            await asyncio.sleep(interval)

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        device.sockets.append(socket)
        while len(device.sockets) > device.max_sockets():
            oldest = device.sockets.pop(0)
            if device.live_socket is oldest:
                device.live_socket = None
            try:
                await oldest.close()
            except Exception:
                pass
        await socket.send_json({"state": device.state, "info": device.info()})
        try:
            while True:
                payload = await socket.receive_json()
                if not isinstance(payload, dict):
                    continue
                if "lv" in payload:
                    if payload["lv"]:
                        device.live_socket = socket
                        asyncio.create_task(stream_live(socket))
                    elif device.live_socket is socket:
                        device.live_socket = None
                    continue
                if not device.authorized(payload):
                    continue
                device.apply_state(payload)
                if payload.get("v"):
                    await socket.send_json({"state": device.state, "info": device.info()})
                await broadcast()
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            if socket in device.sockets:
                device.sockets.remove(socket)
            if device.live_socket is socket:
                device.live_socket = None

    @app.get("/json/state")
    async def get_state() -> dict:
        return device.state

    @app.post("/json/state")
    async def post_state(request: Request):
        payload = await _json_body(request)
        if not device.authorized(payload):
            return JSONResponse({"error": "denied"}, status_code=401)
        device.apply_state(payload)
        await broadcast()
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
        outputs=parse_outputs(os.environ.get("MOCK_OUTPUTS", "")),
    )


def parse_outputs(text: str) -> list[tuple[str, int]]:
    """MOCK_OUTPUTS="Porch rail:40,Porch ceiling:20" gives one output and segment per run."""
    runs: list[tuple[str, int]] = []
    for item in text.split(","):
        if ":" not in item:
            continue
        label, _, length = item.rpartition(":")
        try:
            runs.append((label.strip(), max(1, int(length))))
        except ValueError:
            continue
    return runs


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
