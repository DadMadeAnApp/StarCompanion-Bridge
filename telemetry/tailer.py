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
            with self.path.open("r", encoding="utf-8", errors="replace") as fh:
                for _ in range(20):
                    line = fh.readline()
                    if not line:
                        break
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
        """Follow the file forever, yielding one assembled record at a time."""
        self._last_event = time.monotonic()
        fh = self.path.open("r", encoding="utf-8", errors="replace")
        try:
            fh.seek(0, os.SEEK_END)
            inode = self.path.stat().st_ino
            pending: list[str] = []

            while True:
                line = fh.readline()
                if not line:
                    # A record is only complete once the next one starts, so a
                    # quiet file means the buffered record can be released.
                    if pending:
                        yield "".join(pending).rstrip("\n")
                        pending = []
                    await asyncio.sleep(self.poll_interval)
                    if self._rotated(fh, inode):
                        fh.close()
                        fh = self.path.open("r", encoding="utf-8", errors="replace")
                        inode = self.path.stat().st_ino
                        self._read_header()
                    continue

                self._last_event = time.monotonic()
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
        """
        try:
            stat = self.path.stat()
        except OSError:
            return False
        return stat.st_ino != inode or stat.st_size < fh.tell()
