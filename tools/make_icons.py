"""Draw the PWA icons with the standard library only."""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app" / "static" / "icons"


def write_png(path: Path, size: int, paint) -> None:
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            raw.extend(paint(x, y, size))
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def blend(dst, src) -> tuple[int, int, int, int]:
    sr, sg, sb, sa = src
    if sa <= 0:
        return dst
    if sa >= 255 and dst[3] == 0:
        return src
    amount = sa / 255
    return (
        int(dst[0] * (1 - amount) + sr * amount),
        int(dst[1] * (1 - amount) + sg * amount),
        int(dst[2] * (1 - amount) + sb * amount),
        min(255, int(dst[3] + sa * (1 - dst[3] / 255))),
    )


def paint(x: int, y: int, size: int, maskable: bool) -> tuple[int, int, int, int]:
    nx = (x + 0.5) / size * 2 - 1
    ny = (y + 0.5) / size * 2 - 1
    color = (16, 19, 17, 255) if maskable else (0, 0, 0, 0)
    if not maskable:
        ax = abs(nx)
        ay = abs(ny)
        qx = ax - 0.72
        qy = ay - 0.72
        outside = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0)
        if outside > 0.22:
            return color
        color = (23, 20, 15, 255)
    else:
        color = (23, 20, 15, 255)
    radius = math.hypot(nx, ny)
    scale = 0.72 if maskable else 1
    ring = radius / scale
    if ring <= 0.52:
        color = blend(color, (240, 163, 58, 255))
    if ring <= 0.32:
        color = blend(color, (23, 20, 15, 255))
    if ring <= 0.14:
        color = blend(color, (255, 225, 168, 255))
    return color


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    write_png(ROOT / "icon-192.png", 192, lambda x, y, size: paint(x, y, size, False))
    write_png(ROOT / "icon-512.png", 512, lambda x, y, size: paint(x, y, size, False))
    write_png(ROOT / "apple-touch-icon.png", 180, lambda x, y, size: paint(x, y, size, True))
    write_png(ROOT / "icon-maskable-512.png", 512, lambda x, y, size: paint(x, y, size, True))


if __name__ == "__main__":
    main()
