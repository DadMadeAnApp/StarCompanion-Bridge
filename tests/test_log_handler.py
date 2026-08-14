import logging
import logging.handlers
import os
import sys

import pytest

from core.events import EventBus, LogLine
from ui.log_handler import GuiLogHandler, color_for_level, configure_logging


def _record(level=logging.INFO, msg="hello"):
    return logging.LogRecord("test", level, __file__, 1, msg, None, None)


def test_a_record_becomes_a_logline_event():
    bus = EventBus()
    GuiLogHandler(bus).emit(_record(msg="App connected"))
    events, _ = bus.drain()
    assert len(events) == 1
    assert isinstance(events[0], LogLine)
    assert events[0].text == "App connected"
    assert events[0].level == logging.INFO


def test_the_timestamp_is_wall_clock_hh_mm_ss():
    import re

    bus = EventBus()
    GuiLogHandler(bus).emit(_record())
    events, _ = bus.drain()
    assert re.fullmatch(r"\d\d:\d\d:\d\d", events[0].ts)


def test_formatting_arguments_are_applied():
    bus = EventBus()
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "KEY %s -> %s",
                               ("quantum", "b"), None)
    GuiLogHandler(bus).emit(record)
    events, _ = bus.drain()
    assert events[0].text == "KEY quantum -> b"


def test_levels_map_to_the_palette():
    assert color_for_level(logging.INFO) == "#dfe6ee"
    assert color_for_level(logging.WARNING) == "#fbbf24"
    assert color_for_level(logging.ERROR) == "#f87171"
    assert color_for_level(logging.CRITICAL) == "#f87171"
    assert color_for_level(logging.DEBUG) == "#8a97a6"


def test_a_full_bus_does_not_raise_inside_logging():
    bus = EventBus(maxsize=1)
    handler = GuiLogHandler(bus)
    handler.emit(_record())
    handler.emit(_record())  # dropped, must not raise -- logging swallows nothing here
    events, dropped = bus.drain()
    assert len(events) == 1
    assert dropped == 1


def test_gui_mode_installs_no_stream_handler_so_a_windowed_exe_cannot_crash(monkeypatch, tmp_path):
    # PyInstaller --windowed leaves sys.stdout as None. A StreamHandler built on
    # that raises on the first record, which would kill the very first log line.
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        configure_logging(EventBus(), log_file=tmp_path / "bridge.log")
        assert not any(type(h) is logging.StreamHandler for h in root.handlers)
        logging.info("this must not raise")
    finally:
        root.handlers[:] = saved


def test_gui_mode_writes_to_the_log_file(tmp_path):
    root = logging.getLogger()
    saved = root.handlers[:]
    log_file = tmp_path / "bridge.log"
    try:
        configure_logging(EventBus(), log_file=log_file)
        logging.warning("written to disk")
        for handler in root.handlers:
            handler.flush()
    finally:
        for handler in root.handlers:
            if handler not in saved:
                handler.close()
        root.handlers[:] = saved
    assert "written to disk" in log_file.read_text(encoding="utf-8")


def test_console_mode_installs_a_stream_handler_and_no_gui_handler(tmp_path):
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        configure_logging(None)
        assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
        assert not any(isinstance(h, GuiLogHandler) for h in root.handlers)
    finally:
        root.handlers[:] = saved


@pytest.mark.skipif(sys.platform == "win32", reason="chmod-based perms don't apply on Windows")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                     reason="root ignores directory permissions")
def test_an_unwritable_app_dir_does_not_stop_the_window_only_logging(tmp_path):
    # An install under C:\Program Files is unwritable by a standard user.
    # RotatingFileHandler's constructor raises PermissionError in that case;
    # startup must continue with the pane-only GuiLogHandler already
    # installed rather than crashing before BridgeApp exists.
    root = logging.getLogger()
    saved = root.handlers[:]
    tmp_path.chmod(0o500)
    try:
        configure_logging(EventBus(), log_file=tmp_path / "bridge.log")
        assert any(isinstance(h, GuiLogHandler) for h in root.handlers)
        assert not any(isinstance(h, logging.handlers.RotatingFileHandler)
                        for h in root.handlers)
        logging.warning("this must not raise even though the file can't be opened")
    finally:
        tmp_path.chmod(0o700)
        for handler in root.handlers:
            if handler not in saved:
                handler.close()
        root.handlers[:] = saved


def test_configure_logging_replaces_handlers_rather_than_stacking_them(tmp_path):
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        configure_logging(EventBus(), log_file=tmp_path / "bridge.log")
        first = len(root.handlers)
        configure_logging(EventBus(), log_file=tmp_path / "bridge.log")
        assert len(root.handlers) == first
    finally:
        for handler in root.handlers:
            if handler not in saved:
                handler.close()
        root.handlers[:] = saved
