"""Routes the logging calls already scattered through the server into the window.

Every `logging.info` in bridge_server.py and telemetry/ becomes an activity-pane
line with no edit at the call site, which is the whole reason the activity pane
is a log handler and not a bespoke channel.
"""

from __future__ import annotations

import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.events import EventBus, LogLine

# This palette is duplicated in core/status.py and ui/theme.py.
# Deliberately not consolidated -- see core/status.py's note.
_TEXT = "#dfe6ee"
_MUTED = "#8a97a6"
_WARN = "#fbbf24"
_ERROR = "#f87171"

LEVEL_COLORS = {
    logging.DEBUG: _MUTED,
    logging.INFO: _TEXT,
    logging.WARNING: _WARN,
    logging.ERROR: _ERROR,
    logging.CRITICAL: _ERROR,
}


def color_for_level(level: int) -> str:
    """Nearest colour at or below `level`, so custom levels still map sanely."""
    for threshold in sorted(LEVEL_COLORS, reverse=True):
        if level >= threshold:
            return LEVEL_COLORS[threshold]
    return _MUTED


class GuiLogHandler(logging.Handler):
    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        # A dropped line is acceptable; an exception escaping a log call in the
        # middle of keystroke injection is not.
        try:
            self._bus.emit(LogLine(
                ts=time.strftime("%H:%M:%S", time.localtime(record.created)),
                level=record.levelno,
                text=record.getMessage(),
            ))
        except Exception:  # noqa: BLE001 - handleError is the logging contract
            self.handleError(record)


def configure_logging(bus: EventBus | None, log_file: Path | None = None) -> None:
    """Install handlers for one of the two modes.

    GUI mode (`bus` given) deliberately installs no StreamHandler: under a
    PyInstaller --windowed build sys.stdout is None, and a StreamHandler built
    on it raises on the first record emitted.
    """
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.INFO)

    if bus is None:
        # Console mode: today's behaviour, unchanged.
        stream = sys.stdout if sys.stdout is not None else sys.stderr
        if stream is not None:
            handler = logging.StreamHandler(stream)
            handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
            root.addHandler(handler)
        return

    # Added before the file handler is attempted: an unwritable app_dir (e.g.
    # C:\Program Files under a standard user) must not stop the window from
    # having *some* live log sink, and the warning just below needs somewhere
    # to go.
    root.addHandler(GuiLogHandler(bus))
    if log_file is not None:
        try:
            # 1MB x 2 is plenty to carry a crash traceback to a bug report
            # without letting a long session fill the user's disk.
            file_handler = RotatingFileHandler(
                log_file, maxBytes=1_000_000, backupCount=2, encoding="utf-8",
            )
        except OSError as exc:
            # Most commonly PermissionError from an install under
            # C:\Program Files. Startup must continue with the pane-only
            # handler already installed above rather than crashing before
            # BridgeApp exists.
            logging.warning(f"Could not open {log_file} for logging ({exc}) — "
                             "logging to the window only, not to disk.")
        else:
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", "%Y-%m-%d %H:%M:%S")
            )
            root.addHandler(file_handler)
