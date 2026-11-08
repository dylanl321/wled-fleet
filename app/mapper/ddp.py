"""Distributed Display Protocol sender.

Header is 10 bytes, big-endian: flags, sequence, data type, destination id,
4-byte offset, 2-byte length. Version 1 is 0x40. The push bit 0x01 is set on
the last packet of a frame so WLED draws it. RGB is type 0x0B, RGBW is 0x1B.
Data is split into 480-pixel chunks. Timecode is omitted; WLED ignores it.
"""

from __future__ import annotations

import socket
import struct

DDP_PORT = 4048
DDP_HEADER_LEN = 10
DDP_MAX_PIXELS = 480
DDP_FLAGS_VERSION1 = 0x40
DDP_FLAGS_PUSH = 0x01
DDP_TYPE_RGB = 0x0B
DDP_TYPE_RGBW = 0x1B
DDP_ID_DISPLAY = 1

_HEADER = struct.Struct("!BBBBIH")


def build_packets(payload: bytes, sequence: int, channels: int = 3) -> list[bytes]:
    if channels not in {3, 4}:
        raise ValueError("DDP frames are RGB or RGBW.")
    if len(payload) % channels != 0:
        raise ValueError("DDP payload length has to be a multiple of the channel count.")
    data_type = DDP_TYPE_RGB if channels == 3 else DDP_TYPE_RGBW
    chunk_bytes = DDP_MAX_PIXELS * channels
    packets: list[bytes] = []
    offset = 0
    seq = sequence & 0x0F
    if not payload:
        packets.append(_header(DDP_FLAGS_VERSION1 | DDP_FLAGS_PUSH, seq, data_type, 0, 0) + b"")
        return packets
    while offset < len(payload):
        chunk = payload[offset : offset + chunk_bytes]
        last = offset + len(chunk) >= len(payload)
        flags = DDP_FLAGS_VERSION1 | (DDP_FLAGS_PUSH if last else 0)
        packets.append(_header(flags, seq, data_type, offset, len(chunk)) + chunk)
        offset += len(chunk)
        seq = (seq + 1) & 0x0F
    return packets


def _header(flags: int, sequence: int, data_type: int, offset: int, length: int) -> bytes:
    return _HEADER.pack(flags, sequence & 0x0F, data_type, DDP_ID_DISPLAY, offset, length)


def next_sequence(sequence: int, packet_count: int) -> int:
    return (sequence + packet_count) & 0x0F


def send_frame(
    sock: socket.socket,
    address: tuple[str, int],
    payload: bytes,
    sequence: int,
    channels: int = 3,
) -> int:
    packets = build_packets(payload, sequence, channels)
    for packet in packets:
        sock.sendto(packet, address)
    return next_sequence(sequence, len(packets))


def encode_pixels(colors: dict[int, tuple[int, int, int]], channels: int) -> bytes:
    if not colors:
        return b""
    count = max(colors) + 1
    if count > 20_000:
        raise ValueError("That controller address is too large to stream.")
    buffer = bytearray(count * channels)
    for index, color in colors.items():
        if index < 0:
            continue
        start = index * channels
        buffer[start] = color[0] & 255
        buffer[start + 1] = color[1] & 255
        buffer[start + 2] = color[2] & 255
        if channels == 4:
            buffer[start + 3] = 0
    return bytes(buffer)
