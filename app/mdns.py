from __future__ import annotations

import asyncio
import logging

from zeroconf import IPVersion, ServiceBrowser, Zeroconf

from app.discovery import decode_properties

log = logging.getLogger(__name__)

SERVICE_TYPES = ("_wled._tcp.local.", "_http._tcp.local.")


class _Listener:
    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue):
        self._loop = loop
        self._queue = queue

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._enqueue(type_, name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._enqueue(type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        return None

    def _enqueue(self, type_: str, name: str) -> None:
        try:
            self._loop.call_soon_threadsafe(self._put, type_, name)
        except RuntimeError:
            return

    def _put(self, type_: str, name: str) -> None:
        try:
            self._queue.put_nowait((type_, name))
        except asyncio.QueueFull:
            return


def service_record_from_info(info: object, type_: str, name: str) -> dict | None:
    addresses: list[str] = []
    parsed = getattr(info, "parsed_addresses", None)
    if callable(parsed):
        addresses = [item for item in parsed() if ":" not in item]
    port = int(getattr(info, "port", 80) or 80)
    properties = decode_properties(getattr(info, "properties", {}) or {})
    if not addresses:
        return None
    return {"name": name, "type": type_, "addresses": addresses, "port": port, "properties": properties}


class MdnsWatcher:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=500)
        self.zc: Zeroconf | None = None
        self.browsers: list[ServiceBrowser] = []
        self.ready = False
        self.error: str | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        try:
            self.zc = Zeroconf(ip_version=IPVersion.V4Only)
            listener = _Listener(loop, self.queue)
            self.browsers = [ServiceBrowser(self.zc, service_type, listener) for service_type in SERVICE_TYPES]
            self.ready = True
            self.error = None
            log.info("mDNS listening for %s", ", ".join(SERVICE_TYPES))
        except Exception as exc:
            self.ready = False
            self.error = str(exc)
            log.warning("mDNS is unavailable (%s). Subnet sweep and extra hosts still run.", exc)

    def close(self) -> None:
        for browser in self.browsers:
            try:
                browser.cancel()
            except Exception:
                log.debug("mDNS browser close failed", exc_info=True)
        self.browsers = []
        if self.zc is not None:
            try:
                self.zc.close()
            except Exception:
                log.debug("mDNS close failed", exc_info=True)
        self.zc = None
        self.ready = False

    def drain(self) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        while True:
            try:
                found.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                return found

    async def resolve(self, type_: str, name: str) -> dict | None:
        if self.zc is None:
            return None
        try:
            info = await asyncio.to_thread(self.zc.get_service_info, type_, name, 2000)
        except Exception:
            log.debug("mDNS resolve failed for %s", name, exc_info=True)
            return None
        if info is None:
            return None
        return service_record_from_info(info, type_, name)
