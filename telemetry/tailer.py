"""Follow Star Citizen's live Game.log.

Confirmed 2026-08-10: SC does not hold an exclusive lock and does not sit on a
buffer — the log is readable while the game writes, with sub-second latency. So
a plain shared read plus a short poll is enough; no Windows-specific file
handling is needed.

Only the live file. logbackups\\ is out of scope: archived sessions are dead data
for a second-screen app used during play.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from pathlib import Path
from typing import AsyncIterator, Iterator

from telemetry.parsers import RECORD_START, parse_build

# CIG's own folder is "Roberts Space Industries", but real installs in the wild
# also carry the singular "Robert Space Industries" (the reference machine's F:
# drive is spelled that way) — try both everywhere, never just one.
_INSTALL_PARENTS = ("Roberts Space Industries", "Robert Space Industries")

# LIVE first: it's what a player is almost always running, and preferring a
# stale PTU log over a live one would tail a file nothing is writing.
_CHANNELS = ("LIVE", "PTU", "EPTU", "TECH-PREVIEW", "HOTFIX")

# Prefixes under a drive root where the install parent commonly sits. "" covers
# the very common "moved the whole folder to D:\ or F:\" case.
_ROOT_PREFIXES = ("", "Program Files", "Program Files (x86)", "Games")

_DEFAULT_DIRS = tuple(
    rf"C:\Program Files\{parent}\StarCitizen\LIVE" for parent in _INSTALL_PARENTS
)
STALE_AFTER_SECONDS = 90.0

# The launcher records the install path in plaintext in its own log — the only
# authoritative, user-specific source available, since the "launcher store.json"
# beside it is Electron safeStorage-encrypted and unreadable. Paths appear there
# JSON-escaped ("F:\\Robert Space Industries\\..."), so accept one or two
# backslashes per separator and unescape after matching. Anchored on the
# StarCitizen\<CHANNEL> tail so a match ends at a real directory instead of
# running on into the rest of the log line.
LAUNCHER_LOG = Path.home() / "AppData" / "Roaming" / "rsilauncher" / "logs" / "log.log"
_LAUNCHER_PATH = re.compile(
    r"[A-Za-z]:(?:\\{1,2}[^\\/:*?\"<>|\r\n]+)*?\\{1,2}StarCitizen\\{1,2}"
    # The channel must end on a non-word boundary so "…\LIVESTREAM" is rejected
    # while "…\LIVE now", "…\LIVE\loginData.json" and '…\LIVE"' all match. A
    # lookahead demanding a path separator would be wrong: the log names these
    # dirs mid-sentence far more often than not.
    r"(?:" + "|".join(_CHANNELS) + r")(?![A-Za-z0-9_])"
)


def launcher_log_candidates(text: str) -> list[Path]:
    """Install dirs named in the launcher's log, best candidate first.

    Pure text in, paths out — no I/O, so this tests without a launcher present.
    Later mentions beat earlier ones (the log is append-only, so the newest
    mention reflects the current install), and LIVE outranks every other channel
    regardless of position.
    """
    seen: list[str] = []
    for match in _LAUNCHER_PATH.finditer(text):
        raw = match.group(0).replace("\\\\", "\\")
        if raw not in seen:
            seen.append(raw)
    # Reverse for newest-first, then promote LIVE without disturbing that order
    # (sort is stable, so same-channel candidates keep their newest-first rank).
    ordered = list(reversed(seen))
    ordered.sort(key=lambda p: 0 if p.upper().endswith("LIVE") else 1)
    return [Path(p) for p in ordered]


def _drive_roots() -> list[Path]:
    """Every drive root that exists, C: first."""
    roots = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZAB":
        root = Path(f"{letter}:\\")
        try:
            if root.exists():
                roots.append(root)
        except OSError:
            continue
    return roots


def _guessed_dirs(roots: list[Path]) -> Iterator[Path]:
    """Conventional install dirs under each drive root.

    Deliberately a bounded set of exact paths rather than a recursive scan: a
    full walk of a multi-terabyte drive can take minutes and thrashes the disk
    at the exact moment the user is playing the game.
    """
    for root in roots:
        for prefix in _ROOT_PREFIXES:
            base = root / prefix if prefix else root
            for parent in _INSTALL_PARENTS:
                for channel in _CHANNELS:
                    yield base / parent / "StarCitizen" / channel


def default_log_path(
    launcher_log: Path | None = None,
    roots: list[Path] | None = None,
) -> Path | None:
    """Best guess at the install's Game.log, or None if it can't be found.

    Players move the install off C: routinely (the reference session ran from
    F:\\), so the fixed C:\\Program Files pair is not enough on its own. Three
    sources, cheapest and most authoritative first: the historical defaults, then
    the RSI launcher's own log, then conventional dirs on every drive.

    A None here still becomes the 'log not found' subscribe error, which prompts
    the user for the path — and --log always overrides all of this.
    """
    for base in _DEFAULT_DIRS:
        candidate = Path(base) / "Game.log"
        if candidate.exists():
            return candidate

    log = LAUNCHER_LOG if launcher_log is None else launcher_log
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for directory in launcher_log_candidates(text):
        candidate = directory / "Game.log"
        if candidate.exists():
            return candidate

    for directory in _guessed_dirs(_drive_roots() if roots is None else roots):
        candidate = directory / "Game.log"
        if candidate.exists():
            return candidate
    return None


class LogTailer:
    """Yields whole log records as they are written."""

    def __init__(self, path: Path, poll_interval: float = 0.25) -> None:
        self.path = Path(path)
        self.poll_interval = poll_interval
        self.game_build: str | None = None
        self._last_event = 0.0
        self._read_header()

    def _read_header(self) -> None:
        with contextlib.suppress(OSError):
            with self.path.open("rb") as fh:
                for _ in range(20):
                    raw = fh.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace")
                    build = parse_build(line)
                    if build:
                        self.game_build = build
                        return

    def probe(self) -> None:
        """Actually try to open the file for shared read; raises OSError if not.

        Constructing a LogTailer never fails on its own — `_read_header`
        suppresses OSError so a merely-unreadable log doesn't blow up
        __init__. That means "the file exists but the game (or an AV
        product) has it locked" was previously indistinguishable from
        "readable" until the pump tried to open it and quietly died. Call
        this at subscribe time so that case maps to the documented
        'log locked' ok:false ack instead of a hung subscription.
        """
        self.path.open("rb").close()

    def is_stale(self, now: float | None = None) -> bool:
        """True when nothing has been written for a while — i.e. SC isn't running.

        Not an error: the bridge stays connected and Flight Controls keeps
        working. It drives the 'waiting for Star Citizen' state, which the app
        must show differently from 'bridge disconnected'.
        """
        now = time.monotonic() if now is None else now
        return (now - self._last_event) > STALE_AFTER_SECONDS

    async def watch(self) -> AsyncIterator[str]:
        """Follow the file forever, yielding one assembled record at a time.

        Opened in binary mode and decoded by hand: `TextIOWrapper.tell()`
        returns an opaque cookie, not a byte offset, which is not safe to
        compare against `stat().st_size` (fragile with multi-byte UTF-8 or
        CRLF — and Windows `Game.log` is exactly where CRLF shows up). A
        binary handle's `tell()` is a true byte offset, so the rotation
        size-shrink check is sound.
        """
        self._last_event = time.monotonic()
        fh = self.path.open("rb")
        try:
            fh.seek(0, os.SEEK_END)
            inode = self.path.stat().st_ino
            pending: list[str] = []
            quiet_polls = 0
            lines_read = 0
            # A burst of buffered lines (e.g. catching up after a stall) would
            # otherwise run this whole `while True` synchronously, since the
            # loop only `await`s when a read comes back empty. That starves
            # the same event loop the command dispatcher runs on -- exactly
            # the "telemetry regresses Flight Controls" failure this project
            # can't afford. Yield every _YIELD_EVERY_N_LINES lines: large
            # enough that ordinary per-line ticking almost never pays the
            # extra scheduler round-trip (real bursts are dozens of lines,
            # not thousands), small enough that dispatch latency during a
            # burst stays sub-tick.
            _YIELD_EVERY_N_LINES = 200

            while True:
                raw = fh.readline()
                if not raw:
                    quiet_polls += 1
                    # A wrapped record's continuation can land on the poll
                    # right after the first physical line, so one quiet poll
                    # is not enough to conclude the record is done — wait for
                    # a second consecutive quiet poll before releasing it.
                    # A busy log still flushes immediately (see below), so
                    # only a record followed by silence pays this extra poll.
                    if pending and quiet_polls >= 2:
                        yield "".join(pending).rstrip("\n")
                        pending = []
                    await asyncio.sleep(self.poll_interval)
                    if self._rotated(fh, inode):
                        fh.close()
                        fh = self.path.open("rb")
                        inode = self.path.stat().st_ino
                        self._read_header()
                        pending = []
                        quiet_polls = 0
                    continue

                quiet_polls = 0
                self._last_event = time.monotonic()
                lines_read += 1
                if lines_read % _YIELD_EVERY_N_LINES == 0:
                    await asyncio.sleep(0)
                # Strip the line ending ourselves and re-add a bare '\n': CRLF
                # (routine on the Windows box this actually runs on) would
                # otherwise leave a stray '\r' before every internal join and
                # a trailing one on the record, both of which ride the wire
                # into the "text" field a client renders verbatim.
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n") + "\n"
                if RECORD_START.match(line):
                    if pending:
                        yield "".join(pending).rstrip("\n")
                    pending = [line]
                elif pending:
                    pending.append(line)
        finally:
            fh.close()

    def _rotated(self, fh, inode: int) -> bool:
        """A new session truncates or replaces the file.

        `st_ino` alone is not reliable on Windows (can be 0 or unstable), so
        the size-shrink comparison (`st_size < fh.tell()`) is checked
        independently of the inode check — that alone catches an in-place
        truncation even when the inode looks unchanged or unavailable.
        `fh` is a binary handle here, so `tell()` is a true byte offset and
        the comparison against `st_size` (also bytes) is sound.
        """
        try:
            stat = self.path.stat()
        except OSError:
            return False
        return stat.st_ino != inode or stat.st_size < fh.tell()
