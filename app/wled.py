from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.discovery import is_wled_info, parse_node_targets
from app.errors import WledError
from app.sections import replay_state
from app.util import base_url

log = logging.getLogger(__name__)


class WledClient:
    def __init__(self, http: httpx.AsyncClient, ota_timeout: float = 180.0):
        self.http = http
        self.ota_timeout = ota_timeout

    def _url(self, device: dict, path: str) -> str:
        return base_url(str(device["ip"]), int(device["port"])) + path

    async def _request(self, method: str, device: dict, path: str, **kwargs) -> httpx.Response:
        try:
            response = await self.http.request(method, self._url(device, path), **kwargs)
        except httpx.HTTPError as exc:
            raise WledError(f"Cannot reach {device.get('name') or device['ip']}: {exc.__class__.__name__}") from exc
        if response.status_code == 401:
            text = response.text.strip().splitlines()[0][:180] if response.text else ""
            hint = text or "Unlock its settings PIN or turn off the OTA lock."
            raise WledError(f"Controller refused the request. {hint}", status=401)
        if response.status_code >= 400:
            detail = response.text.strip().splitlines()[0][:180] if response.text else ""
            raise WledError(f"Controller returned {response.status_code} for {path}. {detail}".strip())
        return response

    def _with_pin(self, device: dict, payload: dict) -> dict:
        body = dict(payload)
        pin = device.get("pin")
        if pin and "pin" not in body:
            body["pin"] = pin
        return body

    async def get_info(self, device: dict) -> dict:
        response = await self._request("GET", device, "/json/info", timeout=httpx.Timeout(4.0, connect=2.0))
        try:
            data = response.json()
        except ValueError as exc:
            raise WledError("Controller returned a non-JSON info response.") from exc
        if not is_wled_info(data):
            raise WledError("Device did not return WLED info.")
        return data

    async def get_bytes(self, device: dict, path: str) -> bytes:
        response = await self._request("GET", device, path, timeout=httpx.Timeout(15.0, connect=3.0))
        return response.content

    async def get_cfg_bytes(self, device: dict) -> bytes:
        try:
            return await self.get_bytes(device, "/cfg.json")
        except WledError as exc:
            if "404" not in str(exc):
                raise
        response = await self._request("GET", device, "/json/cfg", timeout=httpx.Timeout(15.0, connect=3.0))
        return response.content

    async def get_presets_bytes(self, device: dict) -> bytes:
        try:
            return await self.get_bytes(device, "/presets.json")
        except WledError as exc:
            if "404" in str(exc):
                return b"{}"
            raise

    async def get_json(self, device: dict, path: str, timeout: float = 8.0) -> dict:
        data = await self.get_json_any(device, path, timeout)
        if not isinstance(data, dict):
            raise WledError(f"Controller returned an unexpected document for {path}.")
        return data

    async def get_json_any(self, device: dict, path: str, timeout: float = 8.0) -> object:
        response = await self._request("GET", device, path, timeout=httpx.Timeout(timeout, connect=2.0))
        try:
            return response.json()
        except ValueError as exc:
            raise WledError(f"Controller returned non-JSON for {path}.") from exc

    async def post_json(self, device: dict, path: str, payload: dict, timeout: float = 12.0) -> dict:
        response = await self._request(
            "POST",
            device,
            path,
            json=self._with_pin(device, payload),
            timeout=httpx.Timeout(timeout, connect=3.0),
        )
        if not response.content:
            return {"success": True}
        try:
            data = response.json()
        except ValueError:
            return {"success": True, "text": response.text[:200]}
        return data if isinstance(data, dict) else {"success": True}

    async def post_state(self, device: dict, payload: dict) -> dict:
        body = dict(payload)
        body.setdefault("v", True)
        if "tt" not in body and "transition" not in body:
            body["tt"] = 2
        data = await self.post_json(device, "/json/state", body)
        state = data.get("state")
        if isinstance(state, dict):
            return state
        return data

    async def post_cfg(self, device: dict, payload: dict) -> dict:
        return await self.post_json(device, "/json/cfg", payload)

    async def unlock(self, device: dict) -> None:
        if device.get("pin"):
            await self.post_json(device, "/json/state", {"pin": device["pin"], "v": False})
        passphrase = device.get("ota_passphrase")
        if passphrase:
            await self.post_cfg(device, {"ota": {"psk": passphrase, "lock": False}})

    async def upload(self, device: dict, filename: str, data: bytes, content_type: str) -> None:
        await self.unlock(device)
        files = {"file": (filename, data, content_type)}
        try:
            await self._request("POST", device, "/upload", files=files, timeout=httpx.Timeout(40.0, connect=5.0))
        except WledError as exc:
            if filename == "cfg.json" and str(exc).startswith("Cannot reach"):
                return
            raise

    async def ota(self, device: dict, filename: str, data: bytes) -> None:
        await self.unlock(device)
        files = {"update": (filename, data, "application/octet-stream")}
        try:
            await self._request(
                "POST",
                device,
                "/update",
                files=files,
                timeout=httpx.Timeout(self.ota_timeout, connect=5.0),
            )
        except WledError as exc:
            if str(exc).startswith("Cannot reach"):
                return
            raise

    async def node_targets(self, device: dict) -> list[tuple[str, int]]:
        try:
            response = await self._request("GET", device, "/json/nodes", timeout=httpx.Timeout(3.0, connect=1.5))
            return parse_node_targets(response.json())
        except (WledError, ValueError):
            return []

    async def wait_ready(self, device: dict, timeout: float = 45.0) -> dict:
        deadline = time.monotonic() + timeout
        saw_down = False
        successes = 0
        last = "no response"
        while time.monotonic() < deadline:
            try:
                info = await self.get_info(device)
            except WledError as exc:
                saw_down = True
                successes = 0
                last = str(exc)
            else:
                successes += 1
                if saw_down or successes >= 2:
                    return info
            await asyncio.sleep(0.4)
        raise WledError(f"Controller did not come back after reboot. {last}")

    async def reboot(self, device: dict) -> dict:
        try:
            await self.post_json(device, "/json/state", {"rb": True, "v": False})
        except WledError as exc:
            if not str(exc).startswith("Cannot reach"):
                raise
        return await self.wait_ready(device)

    async def identify(self, device: dict, seconds: float = 3.0) -> None:
        current = await self.post_json(device, "/json/state", {"v": True, "tt": 0})
        state = current.get("state") if isinstance(current.get("state"), dict) else current
        saved = state if isinstance(state, dict) else {}
        segments = saved.get("seg") if isinstance(saved.get("seg"), list) else []
        ids: list[int] = []
        for index, segment in enumerate(segments):
            if isinstance(segment, dict):
                seg_id = segment.get("id")
                ids.append(seg_id if isinstance(seg_id, int) else index)
        if not ids:
            ids = [0]
        flash = [
            {
                "id": seg_id,
                "fx": 1,
                "sx": 255,
                "frz": False,
                "col": [[255, 255, 255], [0, 0, 0], [0, 0, 0]],
            }
            for seg_id in ids
        ]
        try:
            await self.post_state(device, {"on": True, "bri": 255, "tt": 0, "seg": flash})
            await asyncio.sleep(max(0.0, seconds))
        finally:
            try:
                await self.post_state(device, replay_state(saved))
            except WledError:
                log.warning("could not restore %s after identify", device.get("ip"))

    async def download_backup(self, device: dict) -> tuple[bytes, bytes, dict]:
        cfg, presets, info = await asyncio.gather(
            self.get_cfg_bytes(device),
            self.get_presets_bytes(device),
            self.get_info(device),
        )
        return cfg, presets, info
