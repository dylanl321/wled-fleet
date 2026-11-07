import socket
import threading
import time

import uvicorn

from mock.wled_mock import MockDevice, MockSpec, build_app


class Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        return None


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_mock(spec: MockSpec) -> tuple[Server, int]:
    port = free_port()
    server = Server(uvicorn.Config(build_app(MockDevice(spec)), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return server, port
        time.sleep(0.05)
    raise RuntimeError("mock did not start")


def stop_mock(server: Server) -> None:
    server.should_exit = True
    server.force_exit = True


def esp_image(chip_id: int) -> bytes:
    data = bytearray(32)
    data[0] = 0xE9
    data[1] = 4
    data[2] = 2
    data[8] = 0xEE
    data[12:14] = chip_id.to_bytes(2, "little")
    return bytes(data)


def esp8266_image() -> bytes:
    data = bytearray(32)
    data[0] = 0xE9
    data[1] = 3
    data[2] = 2
    data[8] = 0x40
    return bytes(data)
