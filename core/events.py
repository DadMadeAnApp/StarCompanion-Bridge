"""Events the server thread sends to the GUI thread, and the queue that carries them.

The server runs in a worker thread and must never touch Tk, so every piece of
state the window shows arrives as one of the frozen events below. The bus is
deliberately bounded: at 100 messages/second the server can outrun a 10Hz drain
loop, and a queue that grows without limit would turn a burst into unbounded
memory. Dropping and counting is better than either blocking the server or
losing lines silently.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass

FATAL_PORT_IN_USE = "PORT_IN_USE"
FATAL_CRASH = "CRASH"


@dataclass(frozen=True)
class Ready:
    """The server is bound and listening; everything needed to pair is known."""

    ips: tuple[str, ...]
    port: int
    token: str
    cert_fp: str
    pairing_uri: str


@dataclass(frozen=True)
class ClientCount:
    n: int


@dataclass(frozen=True)
class TelemetryState:
    found: bool
    path: str | None
    feeds: int


@dataclass(frozen=True)
class LogLine:
    ts: str
    level: int
    text: str


@dataclass(frozen=True)
class Fatal:
    """The server thread is dead. `kind` is FATAL_PORT_IN_USE or FATAL_CRASH."""

    kind: str
    message: str


class EventBus:
    """A bounded, thread-safe, never-blocking channel from server to GUI."""

    def __init__(self, maxsize: int = 2000) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=maxsize)
        self._dropped = 0
        # emit() runs on the server thread and drain() on the GUI thread, so the
        # drop counter needs its own lock -- Queue's internal one doesn't cover it.
        self._lock = threading.Lock()

    def emit(self, event) -> bool:
        """Queue an event. Returns False if the bus was full and it was dropped."""
        try:
            self._q.put_nowait(event)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return False
        return True

    def drain(self, limit: int = 200) -> tuple[list, int]:
        """Pull up to `limit` events, plus the number dropped since the last drain."""
        events = []
        for _ in range(limit):
            try:
                events.append(self._q.get_nowait())
            except queue.Empty:
                break
        with self._lock:
            dropped, self._dropped = self._dropped, 0
        return events, dropped
