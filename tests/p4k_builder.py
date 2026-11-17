"""Synthetic Data.p4k / CryXmlB builders for tests.

Everything here is invented test data -- no byte of it comes from a real game
install. Python's zipfile cannot write compression method 100 (Zstandard), nor
the ZIP64 + custom-signature layout a P4K uses, so the archive is assembled by
hand, mirroring the layout core/p4k.py documents.
"""
from __future__ import annotations

import struct
import zlib
import xml.etree.ElementTree as ET


def zstd_available() -> bool:
    try:
        import zstandard
        return hasattr(zstandard, "ZstdCompressor")
    except ImportError:
        return False


def _compress(data: bytes, method: int) -> bytes:
    if method == 0:
        return data
    if method == 8:
        c = zlib.compressobj(wbits=-15)
        return c.compress(data) + c.flush()
    if method == 100:
        import zstandard
        return zstandard.ZstdCompressor().compress(data)
    raise ValueError(method)


def build_p4k(entries, prefix: bytes = b"", zip64: bool = True) -> bytes:
    """entries: iterable of (name, data, method[, encrypted]). Returns archive bytes.

    `prefix` simulates an archive concatenated onto other bytes (offsets are
    then relative to the archive start, like a real concatenated zip).
    """
    entries = list(entries)
    out = bytearray()
    central = bytearray()
    for item in entries:
        name, data, method = item[:3]
        encrypted = item[3] if len(item) > 3 else False
        payload = _compress(data, method)
        if encrypted:
            payload = b"\xde\xad" * 8   # opaque stand-in; the flag alone marks it encrypted
        offset = len(out)
        nb = name.encode()
        out += struct.pack("<4sHHHHHIIIHH", b"PK\x03\x14", 45, 0, method, 0, 0,
                           0, 0xFFFFFFFF, 0xFFFFFFFF, len(nb), 0) + nb + payload
        if zip64:
            body = struct.pack("<QQQ", len(data), len(payload), offset)
            extra = struct.pack("<HH", 1, len(body)) + body
            c_sz = u_sz = off32 = 0xFFFFFFFF
        else:
            extra, c_sz, u_sz, off32 = b"", len(payload), len(data), offset
        if encrypted:
            extra += b"\x00" * (168 - len(extra)) if len(extra) < 168 else b""
            extra += b"\x01" + b"\x00" * 4       # byte 168 non-zero
            assert extra[168] == 1
        central += struct.pack("<4sHHHHHHIIIHHHHHII", b"PK\x01\x02", 45, 45, 0, method, 0, 0,
                               0, c_sz, u_sz, len(nb), len(extra), 0, 0, 0, 0, off32) + nb + extra
    n = len(entries)
    cd_offset = len(out)
    out += central
    if zip64:
        eocd64_off = len(out)
        out += struct.pack("<4sQHHIIQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0, n, n, len(central), cd_offset)
        out += struct.pack("<4sIQI", b"PK\x06\x07", 0, eocd64_off, 1)
        out += struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF,
                           0xFFFFFFFF, 0xFFFFFFFF, 0)
    else:
        out += struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, n, n, len(central), cd_offset, 0)
    return prefix + bytes(out)


def build_cryxmlb(root: ET.Element) -> bytes:
    """Serialise an Element tree as CryXmlB (header 44B, nodes 28B, attrs 8B)."""
    strings = bytearray()
    cache: dict[str, int] = {}

    def s(text: str) -> int:
        if text not in cache:
            cache[text] = len(strings)
            strings.extend(text.encode() + b"\x00")
        return cache[text]

    nodes: list = []
    attrs: list = []
    kids: list = []

    def walk(el, parent):
        idx = len(nodes)
        nodes.append(None)
        a_first = len(attrs)
        for k, v in el.attrib.items():
            attrs.append((s(k), s(v)))
        children = list(el)
        child_idx = [walk(c, idx) for c in children]
        c_first = len(kids)
        kids.extend(child_idx)
        nodes[idx] = (s(el.tag), s(el.text or ""), len(el.attrib), len(children),
                      parent, a_first, c_first)
        return idx

    walk(root, 0xFFFFFFFF)
    node_off = 44
    attr_off = node_off + 28 * len(nodes)
    child_off = attr_off + 8 * len(attrs)
    str_off = child_off + 4 * len(kids)
    blob = bytearray(struct.pack("<8s9I", b"CryXmlB\x00", 0, node_off, len(nodes), attr_off,
                                 len(attrs), child_off, len(kids), str_off, len(strings)))
    for n in nodes:
        blob += struct.pack("<IIHHIII4x", *n)
    for a in attrs:
        blob += struct.pack("<II", *a)
    for k in kids:
        blob += struct.pack("<I", k)
    blob += strings
    return bytes(blob)
