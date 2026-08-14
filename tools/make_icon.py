"""Generates assets/bridge.ico — a placeholder until real artwork exists.

Writes the ICO container and its BMP payloads by hand rather than adding Pillow
for a one-off asset. Run: python tools/make_icon.py
"""

from __future__ import annotations

import struct
from pathlib import Path

BG = (0x14, 0x18, 0x1D)
RING = (0x4A, 0xA8, 0xFF)
SIZES = (16, 32, 48, 64, 256)


def _pixel(x: int, y: int, size: int) -> tuple[int, int, int, int]:
    """A blue ring with a filled centre dot on a transparent field."""
    cx = cy = (size - 1) / 2
    radius = (x - cx) ** 2 + (y - cy) ** 2
    outer = (size * 0.47) ** 2
    inner = (size * 0.33) ** 2
    core = (size * 0.16) ** 2
    if radius <= core:
        return (*RING[::-1], 255)
    if inner < radius <= outer:
        return (*RING[::-1], 255)
    if radius <= outer:
        return (*BG[::-1], 255)
    return (0, 0, 0, 0)


def _bmp(size: int) -> bytes:
    header = struct.pack(
        "<IiiHHIIiiII",
        40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0,
    )
    rows = []
    for y in range(size - 1, -1, -1):  # BMP rows run bottom-up
        rows.append(b"".join(bytes(_pixel(x, y, size)) for x in range(size)))
    mask_row = b"\x00" * (((size + 31) // 32) * 4)
    return header + b"".join(rows) + mask_row * size


def build(path: Path) -> None:
    images = [(size, _bmp(size)) for size in SIZES]
    offset = 6 + 16 * len(images)
    out = [struct.pack("<HHH", 0, 1, len(images))]
    for size, payload in images:
        out.append(struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size, 0 if size >= 256 else size,
            0, 0, 1, 32, len(payload), offset,
        ))
        offset += len(payload)
    out.extend(payload for _, payload in images)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(out))
    print(f"wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    build(Path(__file__).resolve().parent.parent / "assets" / "bridge.ico")
