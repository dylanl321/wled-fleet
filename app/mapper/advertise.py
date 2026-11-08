"""Bonjour advertisement so the phone can find the fleet server."""

from __future__ import annotations

import logging
import socket

from zeroconf import IPVersion, ServiceInfo, Zeroconf

log = logging.getLogger(__name__)

SERVICE_TYPE = "_wledmapper._tcp.local."


def local_ipv4() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class MapperAdvertiser:
    def __init__(self) -> None:
        self.zc: Zeroconf | None = None

    def start(self, port: int) -> None:
        try:
            address = local_ipv4()
            info = ServiceInfo(
                SERVICE_TYPE,
                f"WLED Mapper.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(address)],
                port=int(port),
                properties={b"path": b"/", b"app": b"wled-mapper"},
                server="wled-mapper.local.",
            )
            self.zc = Zeroconf(ip_version=IPVersion.V4Only)
            self.zc.register_service(info)
            log.info("WLED Mapper advertised on %s:%s", address, port)
        except Exception:
            log.warning("WLED Mapper Bonjour advertisement failed", exc_info=True)
            self.close()

    def close(self) -> None:
        if self.zc is None:
            return
        try:
            self.zc.close()
        except Exception:
            log.debug("Bonjour close failed", exc_info=True)
        self.zc = None
