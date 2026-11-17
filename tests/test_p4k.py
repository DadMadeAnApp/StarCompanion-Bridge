import xml.etree.ElementTree as ET

import pytest

from core import p4k
from tests import p4k_builder as pb

needs_zstd = pytest.mark.skipif(not pb.zstd_available(), reason="zstandard not installed")


def _write(tmp_path, entries, **kw):
    path = tmp_path / "Data.p4k"
    path.write_bytes(pb.build_p4k(entries, **kw))
    return path


def test_locate_finds_entries_across_separators_and_case(tmp_path):
    path = _write(tmp_path, [
        ("Data\\Other\\x.txt", b"x", 0),
        ("Data\\Localization\\English\\Global.INI", b"k=v", 0),
    ])
    archive = p4k.P4KArchive(path)
    found = archive.locate(["data/localization/english/global.ini"])
    assert list(found) == ["data/localization/english/global.ini"]
    assert archive.read(next(iter(found.values()))) == b"k=v"


def test_missing_name_is_absent_not_an_error(tmp_path):
    path = _write(tmp_path, [("a.txt", b"a", 0)])
    assert p4k.P4KArchive(path).locate(["nope.txt"]) == {}


def test_deflate_entry(tmp_path):
    data = b"hello deflate " * 50
    path = _write(tmp_path, [("d.bin", data, 8)])
    archive = p4k.P4KArchive(path)
    assert archive.read(archive.locate(["d.bin"])["d.bin"]) == data


@needs_zstd
def test_zstd_entry_and_stored_entry(tmp_path):
    big = b"synthetic line\n" * 500
    path = _write(tmp_path, [("z.bin", big, 100), ("s.bin", b"plain", 0)])
    archive = p4k.P4KArchive(path)
    got = archive.locate(["z.bin", "s.bin"])
    assert archive.read(got["z.bin"]) == big
    assert archive.read(got["s.bin"]) == b"plain"


def test_encrypted_entry_is_refused_and_plain_neighbour_still_reads(tmp_path):
    path = _write(tmp_path, [("e.bin", b"abc", 0, True), ("n.bin", b"n", 0)])
    archive = p4k.P4KArchive(path)
    got = archive.locate(["e.bin", "n.bin"])
    assert got["e.bin"].encrypted and not got["n.bin"].encrypted
    with pytest.raises(p4k.P4KError, match="encrypted; not supported"):
        archive.read(got["e.bin"])
    assert archive.read(got["n.bin"]) == b"n"


def test_non_zip64_archive_and_concatenated_prefix(tmp_path):
    path = _write(tmp_path, [("a.txt", b"aaa", 0)], zip64=False, prefix=b"JUNK" * 10)
    archive = p4k.P4KArchive(path)
    assert archive.read(archive.locate(["a.txt"])["a.txt"]) == b"aaa"


def test_zip64_archive_with_prefix(tmp_path):
    path = _write(tmp_path, [("a.txt", b"aaa", 0)], prefix=b"JUNK" * 10)
    archive = p4k.P4KArchive(path)
    assert archive.read(archive.locate(["a.txt"])["a.txt"]) == b"aaa"


def test_many_entries_stream_through_the_buffer(tmp_path):
    entries = [(f"Data/f{i}.txt", b"x", 0) for i in range(3000)] + [("Data/target.txt", b"hit", 0)]
    path = _write(tmp_path, entries)
    archive = p4k.P4KArchive(path)
    assert archive.read(archive.locate(["data/target.txt"])["data/target.txt"]) == b"hit"


def test_not_an_archive_raises_clear_error(tmp_path):
    path = tmp_path / "Data.p4k"
    path.write_bytes(b"this is not a zip" * 10)
    with pytest.raises(p4k.P4KError, match="end-of-central-directory"):
        p4k.P4KArchive(path).locate(["a"])


def test_unsupported_method_raises(tmp_path):
    path = _write(tmp_path, [("a.txt", b"aaa", 0)])
    archive = p4k.P4KArchive(path)
    entry = archive.locate(["a.txt"])["a.txt"]
    bad = p4k.P4KEntry(**{**entry.__dict__, "method": 14})
    with pytest.raises(p4k.P4KError, match="unsupported compression"):
        archive.read(bad)


def test_get_archive_missing_file(tmp_path):
    with pytest.raises(p4k.P4KError, match="not found"):
        p4k.get_archive(tmp_path / "Data.p4k")


def test_cryxmlb_roundtrip():
    root = ET.Element("profile")
    amap = ET.SubElement(root, "actionmap", name="m1")
    ET.SubElement(amap, "action", name="a1", keyboard="n")
    ET.SubElement(amap, "action", name="a2", keyboard="lalt+b")
    parsed = p4k.parse_xml(pb.build_cryxmlb(root))
    actions = [(a.get("name"), a.get("keyboard")) for a in parsed.iter("action")]
    assert parsed.tag == "profile" and actions == [("a1", "n"), ("a2", "lalt+b")]


def test_plain_xml_with_bom_is_accepted_and_entities_refused():
    assert p4k.parse_xml(b"\xef\xbb\xbf<a b='1'/>").get("b") == "1"
    with pytest.raises(p4k.P4KError):
        p4k.parse_xml(b'<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>')


# ── hardening ─────────────────────────────────────────────────────────────────
import struct
import zlib


def _entry_for(path, name):
    archive = p4k.P4KArchive(path)
    return archive, archive.locate([name])[name]


def test_deflate_bomb_is_capped(tmp_path):
    path = _write(tmp_path, [("b.bin", b"\0" * 100_000, 8)])
    archive, entry = _entry_for(path, "b.bin")
    lying = p4k.P4KEntry(**{**entry.__dict__, "file_size": 10})
    with pytest.raises(p4k.P4KError, match="declared size"):
        archive.read(lying)


@needs_zstd
def test_zstd_bomb_is_capped(tmp_path):
    path = _write(tmp_path, [("b.bin", b"\0" * 100_000, 100)])
    archive, entry = _entry_for(path, "b.bin")
    lying = p4k.P4KEntry(**{**entry.__dict__, "file_size": 10})
    with pytest.raises(p4k.P4KError, match="declared size"):
        archive.read(lying)


def test_eocd_at_file_end_is_p4kerror(tmp_path):
    path = tmp_path / "Data.p4k"
    path.write_bytes(b"x" * 10 + b"PK\x05\x06" + b"\0" * 5)
    with pytest.raises(p4k.P4KError):
        p4k.P4KArchive(path).locate(["a"])


def test_short_zip64_locator_is_p4kerror(tmp_path):
    # A valid-looking EOCD with ZIP64 markers, preceded by a truncated locator.
    eocd = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    path = tmp_path / "Data.p4k"
    path.write_bytes(b"\0" * 4 + eocd)   # <20 bytes precede the EOCD
    with pytest.raises(p4k.P4KError):
        p4k.P4KArchive(path).locate(["a"])


def test_truncated_central_directory_is_p4kerror(tmp_path):
    data = bytearray(pb.build_p4k([("a.txt", b"a", 0), ("b.txt", b"b", 0)], zip64=False))
    # Inflate the declared CD size so the directory is shorter than declared,
    # and shift cd offset back accordingly so the concat check still passes.
    eocd = len(data) - 22
    cd_size, cd_off = struct.unpack_from("<II", data, eocd + 12)
    path = tmp_path / "Data.p4k"
    # Drop the last 10 bytes of the central directory but keep the EOCD declaring the full size.
    cut = bytes(data[:eocd - 10]) + bytes(data[eocd:])
    # eocd moved 10 bytes earlier => concat computes negative-or-shifted; declare size consistent instead:
    cut = bytearray(cut)
    struct.pack_into("<I", cut, len(cut) - 22 + 12, cd_size - 10)
    path.write_bytes(bytes(cut))
    with pytest.raises(p4k.P4KError, match="truncated"):
        p4k.P4KArchive(path).locate(["a.txt", "b.txt"])


def test_cryxmlb_shared_children_are_rejected():
    # 3 nodes: root -> [1, 1]; node 1 -> [2, 2]; node 2 leaf. Depth blowup if unbudgeted.
    strings = b"t\0"
    nodes = [(0, 1, 0, 2, 0xFFFFFFFF, 0, 0), (0, 1, 0, 2, 0, 0, 2), (0, 1, 0, 0, 1, 0, 4)]
    kids = [1, 1, 2, 2]
    node_off = 44
    attr_off = node_off + 28 * 3
    child_off = attr_off
    str_off = child_off + 4 * len(kids)
    blob = struct.pack("<8s9I", b"CryXmlB\x00", 0, node_off, 3, attr_off, 0, child_off, len(kids), str_off, len(strings))
    for n in nodes:
        blob += struct.pack("<IIHHIII4x", *n)
    blob += b"".join(struct.pack("<I", k) for k in kids) + strings
    # content offset 1 is the empty string at the NUL
    with pytest.raises(p4k.P4KError, match="shared or cyclic"):
        p4k.parse_xml(blob)


def test_utf16_doctype_is_rejected():
    xml = '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE r [<!ENTITY a "x">]><r>&a;</r>'
    with pytest.raises(p4k.P4KError, match="DOCTYPE"):
        p4k.parse_xml(xml.encode("utf-16"))


def test_plain_xml_still_parses():
    assert p4k.parse_xml(b"<r a='1'><c/></r>").get("a") == "1"
