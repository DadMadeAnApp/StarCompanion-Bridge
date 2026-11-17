"""Localization resolver: turns game keys and location codes into display text.

The string table is read from ``Data/Localization/english/global.ini`` inside the
user's own ``Data.p4k`` on a background thread, held in memory only, and never
written anywhere. Until (or unless) it loads, every lookup returns None and the
caller degrades to the raw key -- the loader never blocks the event loop, and a
failure is logged once and leaves the bridge unresolved.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from core import p4k

# Location-code -> global.ini key variants, tried in order after the code itself.
# UNVERIFIED: which key actually holds a place's display name is not known. These
# are plausible guesses; confirm against a real install on the Windows PC and
# trim/extend. `{code}` is the whole code (Stanton4_NewBabbage), `{tail}` is
# everything after the first underscore (NewBabbage).
LOCATION_KEY_VARIANTS = (
    "{code}",
    "{code}_name",
    "loc_{code}",
    "ui_{code}",
    "{tail}",
    "{tail}_name",
    "loc_{tail}",
)

_AT_TOKEN = re.compile(r"(?<![\w@])@([A-Za-z0-9_]+)")


def parse_ini(text: str) -> dict[str, str]:
    """Parse global.ini text into {casefolded key: value}.

    Lines are ``key=value``; lines without ``=`` are ignored. Some keys carry a
    trailing ``,P`` suffix (UNVERIFIED meaning); both the full key and the key
    without it are indexed, the full key winning on a clash.
    """
    table: dict[str, str] = {}
    stripped: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().casefold()
        if not key:
            continue
        table[key] = value
        if key.endswith(",p"):
            stripped.setdefault(key[:-2], value)
    for key, value in stripped.items():
        table.setdefault(key, value)
    return table


def _usable(value: str | None, key: str) -> str | None:
    """A resolved value worth showing: non-empty, not a placeholder, not the key."""
    if value is None:
        return None
    value = value.strip()
    if not value or value.startswith("@"):
        return None
    if "PLACEHOLDER" in value.upper():
        return None
    if value.casefold() == key.casefold():
        return None
    return value


class Localization:
    """In-memory string table with a background loader."""

    def __init__(self) -> None:
        self._table: dict[str, str] | None = None
        self._lock = threading.Lock()
        self._source: tuple | None = None
        self._failed_logged = False

    # -- loading ---------------------------------------------------------
    def load_text(self, text: str) -> None:
        """Install a table directly (used by the loader and by tests)."""
        table = parse_ini(text)
        with self._lock:
            self._table = table

    def start(self, p4k_path: Path | None) -> None:
        """Begin loading from `p4k_path` unless that source is already loading/loaded."""
        with self._lock:
            if p4k_path is None:
                self._source = None
                self._table = None
                return
            p4k_path = Path(p4k_path)
            # Identity includes size/mtime so a patched p4k (or one that
            # appeared after a failed first attempt) is loaded afresh.
            try:
                st = p4k_path.stat()
                ident = (p4k_path, st.st_size, st.st_mtime_ns)
            except OSError:
                ident = (p4k_path, None, None)
            if ident == self._source:
                return
            self._source = ident
            self._table = None
            self._failed_logged = False
        threading.Thread(
            target=self._load, args=(p4k_path, ident), name="p4k-localization", daemon=True
        ).start()

    def _load(self, path: Path, ident: tuple) -> None:
        try:
            raw = p4k.read_named(path, p4k.GLOBAL_INI)
            table = parse_ini(raw.decode("utf-8-sig", errors="replace"))
        except p4k.P4KError as exc:
            self._log_failure(path, str(exc))
            return
        except Exception as exc:  # never let a loader bug reach the event loop
            self._log_failure(path, f"{type(exc).__name__}: {exc}")
            return
        with self._lock:
            if self._source != ident:
                return          # superseded by a newer start()
            self._table = table
        logging.info(f"[names] Loaded {len(table)} game strings for name resolution")

    def _log_failure(self, path: Path, why: str) -> None:
        with self._lock:
            if self._failed_logged:
                return
            self._failed_logged = True
        logging.warning(f"[names] Name resolution unavailable ({why}); showing raw game keys")

    @property
    def ready(self) -> bool:
        return self._table is not None

    # -- lookups ---------------------------------------------------------
    def resolve_key(self, key: str) -> str | None:
        table = self._table
        if table is None or not isinstance(key, str):
            return None
        key = key.strip()
        if key.startswith("@"):
            key = key[1:]
        if not key:
            return None
        return _usable(table.get(key.casefold()), key)

    def resolve_location(self, code: str) -> str | None:
        if self._table is None or not isinstance(code, str) or not code.strip():
            return None
        code = code.strip()
        tail = code.split("_", 1)[1] if "_" in code else ""
        seen: set[str] = set()
        for template in LOCATION_KEY_VARIANTS:
            if "{tail}" in template and not tail:
                continue
            candidate = template.format(code=code, tail=tail)
            if candidate.casefold() in seen:
                continue
            seen.add(candidate.casefold())
            found = self.resolve_key(candidate)
            if found:
                return found
        return None

    def resolve_text(self, text: str) -> str:
        """Replace standalone ``@key`` tokens that resolve; leave everything else.

        ``~mission(...)`` / ``~action(...)`` runtime placeholders never match
        (they do not start with ``@``) and are left exactly as written.
        """
        if self._table is None or "@" not in text:
            return text

        def sub(match: re.Match) -> str:
            return self.resolve_key(match.group(1)) or match.group(0)

        return _AT_TOKEN.sub(sub, text)


# Process-wide instance shared by the server and the keybind exporter.
resolver = Localization()
