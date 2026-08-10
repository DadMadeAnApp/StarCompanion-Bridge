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
import time
from pathlib import Path
from typing import AsyncIterator

from telemetry.parsers import RECORD_START, parse_build

# Note the folder is "Robert Space Industries", not "Roberts".
_DEFAULT_DIRS = (
    r"C:\Program Files\Roberts Space Industries\StarCitizen\LIVE",
    r"C:\Program Files\Robert Space Industries\StarCitizen\LIVE",
)
STALE_AFTER_SECONDS = 90.0


def default_log_path() -> Path | None:
    """Best guess at the install's Game.log, or None if it can't be found.

    The bridge cannot always infer the install path — players move it to another
    drive routinely (the reference session ran from F:\\). A None here becomes the
    'log not found' subscribe error, which prompts the user for the path.
    """
    for base in _DEFAULT_DIRS:
        candidate = Path(base) / "Game.log"
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
