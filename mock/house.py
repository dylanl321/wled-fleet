"""A whole house of mock controllers in one process, for demos and screenshot checks.

    python -m mock.house            # boards on ports 9101 and up
    EXTRA_HOSTS=$(python -m mock.house --hosts) python -m app

The layout mirrors a real install: several QuinLED boards with two to four
outputs each, one ESP8266, and one board still called "WLED" waiting to be
set up. About 25 outputs in all.
"""

from __future__ import annotations

import os
import sys
import threading
import time

import uvicorn

from mock.wled_mock import MockDevice, MockSpec, build_app

BASE_PORT = int(os.environ.get("HOUSE_PORT", "9101"))


def _buses(*lengths: int) -> tuple[tuple[int, int, int], ...]:
    out = []
    start = 0
    for index, length in enumerate(lengths):
        out.append((start, length, 16 + index))
        start += length
    return tuple(out)


HOUSE: list[MockSpec] = [
    MockSpec(mac="a0c4f1000001", name="Eaves", mdns="eaves", arch="esp32", release="Dig-Quad-V3", version="0.15.1", leds=600, buses=_buses(150, 150, 150, 150)),
    MockSpec(mac="a0c4f1000002", name="Well", mdns="well", arch="esp32", release="Dig-Quad-V3", version="0.15.1", leds=240, buses=_buses(60, 60, 60, 60)),
    MockSpec(mac="a0c4f1000003", name="Mailbox Tree", mdns="mailbox-tree", arch="esp32", release="Dig-Quad-V3", version="0.15.1", leds=400, buses=_buses(100, 100, 100, 100)),
    MockSpec(mac="a0c4f1000004", name="Roofline", mdns="roofline", arch="esp32", release="Dig-Quad-V3", version="0.15.1", leds=800, buses=_buses(200, 200, 200, 200)),
    MockSpec(mac="a0c4f1000005", name="Front Door", mdns="front-door", arch="esp32", release="Dig-Uno-V3", version="0.15.1", leds=120, buses=_buses(60, 60)),
    MockSpec(mac="a0c4f1000006", name="Dig_Next_2", mdns="dig-next-2", arch="esp32", release="Dig-Next-2", version="0.15.1", leds=200, buses=_buses(100, 100)),
    MockSpec(mac="a0c4f1000007", name="Garage", mdns="garage", arch="esp32", release="Dig-Uno-V3", version="0.14.4", leds=180, buses=_buses(90, 90)),
    MockSpec(mac="a0c4f1000008", name="Porch", mdns="porch", arch="esp8266", release="ESP8266", version="0.14.4", leds=60),
    MockSpec(mac="a0c4f1000009", name="WLED", mdns="wled-a3f2", arch="esp32", release="ESP32", version="0.15.1", leds=30),
]


def hosts(base: int = BASE_PORT) -> str:
    return ",".join(f"127.0.0.1:{base + index}" for index in range(len(HOUSE)))


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        return None


def serve(base: int = BASE_PORT) -> list[_Server]:
    servers = []
    for index, spec in enumerate(HOUSE):
        server = _Server(uvicorn.Config(build_app(MockDevice(spec)), host="127.0.0.1", port=base + index, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        servers.append(server)
    return servers


def main() -> None:
    if "--hosts" in sys.argv:
        print(hosts())
        return
    serve()
    outputs = sum(max(1, len(spec.buses)) for spec in HOUSE)
    print(f"{len(HOUSE)} controllers, {outputs} outputs. EXTRA_HOSTS={hosts()}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
