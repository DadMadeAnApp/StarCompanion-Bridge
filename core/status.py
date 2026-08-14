"""The one place that decides what the header dot and text say.

Keeping this out of the widget layer means every state transition is testable
without a display, and there is no second opinion about what colour "connected"
is.
"""

from __future__ import annotations

import re

from core.events import FATAL_PORT_IN_USE, ClientCount, Fatal, Ready

STARTING = "STARTING"
LISTENING = "LISTENING"
CONNECTED = "CONNECTED"
PORT_IN_USE = "PORT_IN_USE"
ERROR = "ERROR"

_GREY = "#6b7785"
_BLUE = "#4aa8ff"
_GREEN = "#4ade80"
_RED = "#f87171"

_COLORS = {
    STARTING: _GREY,
    LISTENING: _BLUE,
    CONNECTED: _GREEN,
    PORT_IN_USE: _RED,
    ERROR: _RED,
}

_PORT_IN_MESSAGE = re.compile(r"port (\d+)")


class StatusModel:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Back to STARTING, clearing any latched failure. Used by Retry/Restart."""
        self._status = STARTING
        self._clients = 0
        self._port: int | None = None

    def apply(self, event) -> None:
        # A fatal state latches. The server thread is dead by then, and an event
        # it queued just before dying must not paint the window as healthy.
        if self.fatal:
            return
        if isinstance(event, Ready):
            self._port = event.port
            self._status = LISTENING
        elif isinstance(event, ClientCount):
            self._clients = event.n
            self._status = CONNECTED if event.n > 0 else LISTENING
        elif isinstance(event, Fatal):
            if event.kind == FATAL_PORT_IN_USE:
                self._status = PORT_IN_USE
                if self._port is None:
                    found = _PORT_IN_MESSAGE.search(event.message)
                    self._port = int(found.group(1)) if found else None
            else:
                self._status = ERROR

    @property
    def status(self) -> str:
        return self._status

    @property
    def clients(self) -> int:
        return self._clients

    @property
    def fatal(self) -> bool:
        return self._status in (PORT_IN_USE, ERROR)

    @property
    def color(self) -> str:
        return _COLORS[self._status]

    @property
    def text(self) -> str:
        if self._status == STARTING:
            return "Starting…"
        if self._status == LISTENING:
            return "Listening — waiting for app"
        if self._status == CONNECTED:
            plural = "app" if self._clients == 1 else "apps"
            return f"Connected — {self._clients} {plural}"
        if self._status == PORT_IN_USE:
            where = f"Port {self._port}" if self._port else "Port"
            return f"{where} in use — is the bridge already running?"
        return "Error — see activity log"
