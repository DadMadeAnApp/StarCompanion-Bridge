"""Read-only access to Star Citizen's Data.p4k, plus a CryXmlB reader.

LEGAL BOUNDARY (standing owner decision): game data is read at runtime from the
user's own install and kept in memory. Nothing extracted here is ever written to
disk, committed, bundled, or sent over the socket in bulk -- callers pass on
only resolved strings and individual bindings.

Format notes, from the open-source readers this was written against
(scdatatools `p4k.py` / `engine/cryxml`, and unp4k):

- A P4K is a ZIP64 archive of roughly 100 GB. Only the end-of-central-directory
  records and the central directory are parsed; entries are then fetched by
  seeking straight to their local header. The central directory is scanned in
  a streaming pass that keeps only the entries a caller asked for, because
  holding every entry of a million-file archive as Python objects would cost
  hundreds of megabytes for no benefit.
- Local file headers carry the signature ``PK\\x03\\x14`` (CIG's variant) or the
  ordinary ``PK\\x03\\x04``; both are accepted.
- Compression method 100 is Zstandard; 0 (stored) and 8 (deflate) also occur.
  Anything else raises.
- Some entries are flagged encrypted (byte 168 of the central-directory extra
  field is non-zero). Those are deliberately NOT read: ``read()`` raises
  ``P4KError`` before touching the payload (owner decision, 2026-11-17).
- Sizes and the local-header offset live in the ZIP64 extra record (tag 0x0001)
  whenever the 32-bit field holds 0xFFFFFFFF.

UNVERIFIED on a real install (check on the Windows PC): the byte-168
encrypted flag and the exact entry names -- see the constants in this module.
"""

from __future__ import annotations

import io
import struct
import threading
import xml.etree.ElementTree as ET
import xml.parsers.expat as expat
import zlib
from dataclasses import dataclass
from pathlib import Path

ZIP_STORED = 0
ZIP_DEFLATED = 8
ZIP_ZSTD = 100

_SIG_LOCAL = (b"PK\x03\x04", b"PK\x03\x14")
_SIG_CD = b"PK\x01\x02"
_SIG_EOCD = b"PK\x05\x06"
_SIG_EOCD64 = b"PK\x06\x06"
_SIG_LOC64 = b"PK\x06\x07"

_CD_STRUCT = struct.Struct("<4sHHHHHHIIIHHHHHII")      # 46 bytes
_LOCAL_STRUCT = struct.Struct("<4sHHHHHIIIHH")           # 30 bytes
_EOCD_STRUCT = struct.Struct("<4sHHHHIIH")               # 22 bytes
_EOCD64_STRUCT = struct.Struct("<4sQHHIIQQQQ")           # 56 bytes
_LOC64_STRUCT = struct.Struct("<4sIQI")                  # 20 bytes

# Refuse absurd entries rather than allocate gigabytes for a corrupt header.
MAX_ENTRY_BYTES = 256 * 1024 * 1024

# Entries the bridge needs. Matching is case-insensitive with "\\" == "/".
GLOBAL_INI = "Data/Localization/english/global.ini"
DEFAULT_PROFILE = "Data/Libs/Config/defaultProfile.xml"
WANTED = (GLOBAL_INI, DEFAULT_PROFILE)


class P4KError(Exception):
    """The archive (or one entry) could not be read. Message is human-readable."""


def norm_name(name: str) -> str:
    return name.replace("\\", "/").casefold()


@dataclass(frozen=True)
class P4KEntry:
    name: str
    method: int
    flags: int
    compress_size: int
    file_size: int
    header_offset: int
    encrypted: bool


def _parse_zip64_extra(extra: bytes, file_size: int, csize: int, offset: int):
    """Resolve the 0xFFFFFFFF sentinels from the ZIP64 extra record, if any."""
    pos = 0
    while pos + 4 <= len(extra):
        tag, size = struct.unpack_from("<HH", extra, pos)  # guarded by pos + 4 <= len
        body = extra[pos + 4: pos + 4 + size]
        if tag == 0x0001:
            vals = [struct.unpack_from("<Q", body, i)[0] for i in range(0, len(body) - 7, 8)]  # i+8 <= len(body)
            idx = 0
            if file_size == 0xFFFFFFFF and idx < len(vals):
                file_size = vals[idx]; idx += 1
            if csize == 0xFFFFFFFF and idx < len(vals):
                csize = vals[idx]; idx += 1
            if offset == 0xFFFFFFFF and idx < len(vals):
                offset = vals[idx]; idx += 1
            break
        pos += 4 + size
    return file_size, csize, offset


class P4KArchive:
    """One Data.p4k. Cheap to construct; the central directory is scanned lazily."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._index: dict[str, P4KEntry] = {}
        self._scanned: frozenset[str] = frozenset()

    # -- end records ---------------------------------------------------------
    def _central_directory(self, fh) -> tuple[int, int, int]:
        """(start, size, concat) of the central directory, ZIP64-aware.

        `concat` is the number of bytes the archive is shifted by when it was
        appended to other data; every stored offset must have it added.
        """
        fh.seek(0, 2)
        size = fh.tell()
        tail_len = min(size, 65536 + _EOCD_STRUCT.size + _LOC64_STRUCT.size)
        fh.seek(size - tail_len)
        tail = fh.read(tail_len)
        pos = tail.rfind(_SIG_EOCD)
        if pos < 0:
            raise P4KError("Data.p4k is not a valid archive (no end-of-central-directory record)")
        if len(tail) - pos < _EOCD_STRUCT.size:
            raise P4KError("Data.p4k end-of-central-directory record is truncated")
        eocd = _EOCD_STRUCT.unpack_from(tail, pos)
        cd_size, cd_offset = eocd[5], eocd[6]
        eocd_abs = size - tail_len + pos

        needs64 = 0xFFFFFFFF in (cd_size, cd_offset) or eocd[3] == 0xFFFF
        loc_abs = eocd_abs - _LOC64_STRUCT.size
        if loc_abs >= 0:
            fh.seek(loc_abs)
            loc = fh.read(_LOC64_STRUCT.size)
            if len(loc) == _LOC64_STRUCT.size and loc[:4] == _SIG_LOC64:
                needs64 = True
        if needs64:
            if loc_abs < 0:
                raise P4KError("Data.p4k has a ZIP64 marker but no ZIP64 locator")
            fh.seek(loc_abs)
            raw_loc = fh.read(_LOC64_STRUCT.size)
            if len(raw_loc) != _LOC64_STRUCT.size:
                raise P4KError("Data.p4k ZIP64 locator is truncated")
            loc = _LOC64_STRUCT.unpack(raw_loc)
            if loc[0] != _SIG_LOC64:
                raise P4KError("Data.p4k ZIP64 locator is missing")
            # The ZIP64 end record normally sits right before its locator; trust
            # that over the locator's stored offset, which is wrong when the
            # archive is concatenated onto other bytes.
            rec = b""
            for cand in (loc_abs - _EOCD64_STRUCT.size, loc[2]):
                if cand < 0:
                    continue
                fh.seek(cand)
                rec = fh.read(_EOCD64_STRUCT.size)
                if len(rec) == _EOCD64_STRUCT.size and rec[:4] == _SIG_EOCD64:
                    eocd_abs = cand
                    break
            else:
                raise P4KError("Data.p4k ZIP64 end record is missing")
            e64 = _EOCD64_STRUCT.unpack(rec)
            cd_size, cd_offset = e64[8], e64[9]
        # Archive concatenated onto something else: the stored offset is relative.
        concat = eocd_abs - cd_size - cd_offset
        if concat < 0:
            raise P4KError("Data.p4k central directory is inconsistent")
        return cd_offset + concat, cd_size, concat

    # -- central directory scan ---------------------------------------------
    def locate(self, names=WANTED) -> dict[str, P4KEntry]:
        """Find entries by name in one streaming pass over the central directory.

        Returns {normalised requested name: entry}; names not in the archive are
        absent. Repeated calls for names already scanned are free.
        """
        wanted = {norm_name(n) for n in names}
        with self._lock:
            if wanted <= self._scanned:
                return {n: self._index[n] for n in wanted if n in self._index}
            try:
                with self.path.open("rb") as fh:
                    start, size, concat = self._central_directory(fh)
                    self._scan(fh, start, size, concat, wanted)
            except OSError as exc:
                raise P4KError(f"could not read Data.p4k: {exc.strerror or exc}") from exc
            except struct.error as exc:
                raise P4KError(f"Data.p4k is corrupt ({exc})") from exc
            self._scanned = self._scanned | frozenset(wanted)
            return {n: self._index[n] for n in wanted if n in self._index}

    def _scan(self, fh, start: int, size: int, concat: int, wanted: set[str]) -> None:
        fh.seek(start)
        remaining = size
        buf = b""
        chunk = 8 * 1024 * 1024
        pos = 0
        while True:
            # Top the buffer up so at least one whole record is available.
            if len(buf) - pos < _CD_STRUCT.size + 3 * 65535 and remaining > 0:
                data = fh.read(min(chunk, remaining))
                if not data:
                    remaining = 0
                else:
                    remaining -= len(data)
                    buf = buf[pos:] + data
                    pos = 0
            if len(buf) - pos < _CD_STRUCT.size:
                if remaining > 0 or len(buf) - pos != 0:
                    raise P4KError("Data.p4k central directory is truncated")
                break
            rec = _CD_STRUCT.unpack_from(buf, pos)
            if rec[0] != _SIG_CD:
                raise P4KError("Data.p4k central directory is corrupt (bad record signature)")
            nlen, elen, clen = rec[10], rec[11], rec[12]
            total = _CD_STRUCT.size + nlen + elen + clen
            if len(buf) - pos < total:
                if remaining == 0:
                    raise P4KError("Data.p4k central directory is truncated")
                continue  # loop tops the buffer up
            name_bytes = buf[pos + _CD_STRUCT.size: pos + _CD_STRUCT.size + nlen]
            key = norm_name(name_bytes.decode("utf-8", errors="replace"))
            if key in wanted:
                extra = buf[pos + _CD_STRUCT.size + nlen: pos + _CD_STRUCT.size + nlen + elen]
                fsize, csize, off = _parse_zip64_extra(extra, rec[9], rec[8], rec[16])
                self._index[key] = P4KEntry(
                    name=name_bytes.decode("utf-8", errors="replace"),
                    method=rec[4], flags=rec[3],
                    compress_size=csize, file_size=fsize, header_offset=off + concat,
                    encrypted=len(extra) > 168 and extra[168] > 0,
                )
            pos += total

    # -- entry extraction ----------------------------------------------------
    def read(self, entry: P4KEntry) -> bytes:
        """Return one entry's decompressed bytes (in memory only).

        Entries flagged encrypted are refused without reading their payload.
        """
        if entry.encrypted:
            raise P4KError("entry is encrypted; not supported")
        if entry.file_size > MAX_ENTRY_BYTES or entry.compress_size > MAX_ENTRY_BYTES:
            raise P4KError(f"{entry.name}: entry is implausibly large")
        try:
            with self.path.open("rb") as fh:
                fh.seek(entry.header_offset)
                head = fh.read(_LOCAL_STRUCT.size)
                if len(head) != _LOCAL_STRUCT.size:
                    raise P4KError(f"{entry.name}: truncated local header")
                try:
                    loc = _LOCAL_STRUCT.unpack(head)
                except struct.error as exc:  # pragma: no cover - length checked above
                    raise P4KError(f"{entry.name}: bad local header ({exc})") from exc
                if loc[0] not in _SIG_LOCAL:
                    raise P4KError(f"{entry.name}: bad local header signature")
                fh.seek(loc[9] + loc[10], 1)
                raw = fh.read(entry.compress_size)
        except OSError as exc:
            raise P4KError(f"could not read Data.p4k: {exc.strerror or exc}") from exc
        if len(raw) != entry.compress_size:
            raise P4KError(f"{entry.name}: data is truncated")
        return _decompress(entry, raw)


def _decompress(entry: P4KEntry, raw: bytes) -> bytes:
    try:
        if entry.method == ZIP_STORED:
            out = raw
        elif entry.method == ZIP_DEFLATED:
            out = zlib.decompressobj(-15).decompress(raw, entry.file_size + 1)
        elif entry.method == ZIP_ZSTD:
            try:
                import zstandard
            except ImportError as exc:  # pragma: no cover
                raise P4KError("the 'zstandard' package is required to read Data.p4k") from exc
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)) as rd:
                out = rd.read(entry.file_size + 1)
        else:
            raise P4KError(f"{entry.name}: unsupported compression method {entry.method}")
    except (zlib.error, ValueError) as exc:
        raise P4KError(f"{entry.name}: could not decompress ({exc})") from exc
    except P4KError:
        raise
    except Exception as exc:  # zstandard.ZstdError and friends
        raise P4KError(f"{entry.name}: could not decompress ({exc})") from exc
    # Stored data is raw and cannot amplify; only inflated output is capped.
    if entry.method != ZIP_STORED and len(out) > entry.file_size:
        raise P4KError(f"{entry.name}: decompressed past its declared size ({entry.file_size} bytes)")
    if len(out) < entry.file_size:
        raise P4KError(f"{entry.name}: decompressed to {len(out)} bytes, expected {entry.file_size}")
    return out[: entry.file_size]


# ── Archive registry ───────────────────────────────────────────────────────────
_archives: dict[Path, tuple[tuple[int, int], P4KArchive]] = {}
_archives_lock = threading.Lock()


def get_archive(path: Path) -> P4KArchive:
    """A shared archive object per file, replaced when the file changes.

    Sharing means the multi-second central-directory scan happens once for both
    the localization loader and keybind export; a game patch (new size/mtime)
    invalidates it.
    """
    path = Path(path)
    try:
        st = path.stat()
    except OSError as exc:
        raise P4KError("Data.p4k not found") from exc
    sig = (st.st_size, st.st_mtime_ns)
    with _archives_lock:
        cached = _archives.get(path)
        if cached and cached[0] == sig:
            return cached[1]
        archive = P4KArchive(path)
        _archives[path] = (sig, archive)
        return archive


def read_named(path: Path, name: str) -> bytes:
    """Convenience: locate and read one entry by name."""
    archive = get_archive(path)
    entries = archive.locate(WANTED if norm_name(name) in {norm_name(n) for n in WANTED} else (name,))
    entry = entries.get(norm_name(name))
    if entry is None:
        raise P4KError(f"{name} is not in Data.p4k")
    return archive.read(entry)


# ── CryXmlB ─────────────────────────────────────────────────────────────────────
_CRYXML_SIG = b"CryXmlB"
_CRYXML_HEADER = struct.Struct("<8s9I")     # signature + 9 uint32 = 44 bytes
_CRYXML_NODE = struct.Struct("<IIHHIII4x")   # 28 bytes
_CRYXML_ATTR = struct.Struct("<II")
NO_PARENT = 0xFFFFFFFF


def parse_xml(data: bytes) -> ET.Element:
    """Parse plain XML or CIG's binary CryXmlB into an ElementTree element."""
    if data[:7] == _CRYXML_SIG:
        return _parse_cryxmlb(data)
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    # SC's XML never declares entities. The substring check is only a fast path
    # (UTF-16 input slips past it); the expat handlers below are the real guard
    # and reject any DTD/entity declaration regardless of encoding.
    if b"<!ENTITY" in data or b"<!DOCTYPE" in data:
        raise P4KError("XML with a DOCTYPE/ENTITY declaration is not accepted")

    def _refuse(*_a):
        raise P4KError("XML with a DOCTYPE/ENTITY declaration is not accepted")

    guard = expat.ParserCreate()
    guard.StartDoctypeDeclHandler = _refuse
    guard.EntityDeclHandler = _refuse
    try:
        guard.Parse(data, True)       # syntax errors are reported by ET below
    except expat.ExpatError:
        pass
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise P4KError(f"not valid XML ({exc})") from exc


def _parse_cryxmlb(data: bytes) -> ET.Element:
    try:
        (_sig, _xml_size, node_off, node_n, attr_off, attr_n,
         child_off, child_n, str_off, str_size) = _CRYXML_HEADER.unpack_from(data, 0)

        def string(off: int) -> str:
            start = str_off + off
            end = data.index(b"\x00", start, str_off + str_size)
            return data[start:end].decode("utf-8", errors="replace")

        attrs = [_CRYXML_ATTR.unpack_from(data, attr_off + i * 8) for i in range(attr_n)]
        kids = struct.unpack_from(f"<{child_n}I", data, child_off) if child_n else ()
        nodes = [_CRYXML_NODE.unpack_from(data, node_off + i * 28) for i in range(node_n)]

        # A well-formed tree visits each node once. Shared child indices could
        # fan out exponentially, so cap total elements built at the node count.
        budget = [node_n]

        def build(index: int, depth: int = 0) -> ET.Element:
            if depth > 256:
                raise P4KError("CryXmlB nesting is implausibly deep")
            budget[0] -= 1
            if budget[0] < 0:
                raise P4KError("CryXmlB child indices are shared or cyclic")
            tag_o, content_o, a_count, c_count, _parent, a_first, c_first = nodes[index]
            el = ET.Element(string(tag_o))
            for k, v in attrs[a_first: a_first + a_count]:
                el.set(string(k), string(v))
            text = string(content_o)
            if text:
                el.text = text
            for ci in kids[c_first: c_first + c_count]:
                el.append(build(ci, depth + 1))
            return el

        if node_n == 0:
            raise P4KError("CryXmlB has no nodes")
        return build(0)
    except (struct.error, ValueError, IndexError, RecursionError) as exc:
        raise P4KError(f"corrupt CryXmlB ({exc})") from exc
