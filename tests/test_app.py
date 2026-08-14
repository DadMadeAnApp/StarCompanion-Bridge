import logging
import sys
import threading
import types

import pytest


def _install_pynput_stub_if_needed():
    """Stub pynput before ui.app pulls in bridge_server, same as test_bridge_server.py.

    ui.app imports bridge_server, whose SPECIAL_KEYS table is built at import
    time and references Key members (e.g. Key.insert) that macOS's real
    pynput backend does not define. Without this, importing ui.app here
    raises AttributeError on this platform -- and once the real pynput module
    is cached in sys.modules, test_bridge_server.py's own stub-install (guarded
    on "already in sys.modules") would silently no-op and hit the same error.
    Collection order is alphabetical, so this file runs first and must not
    leave a poisoned sys.modules behind for the rest of the suite.
    """
    if "pynput" in sys.modules:
        return

    pynput = types.ModuleType("pynput")
    keyboard_mod = types.ModuleType("pynput.keyboard")
    mouse_mod = types.ModuleType("pynput.mouse")

    class _KeySentinel:
        def __init__(self, name):
            self._name = name
            self.name = name

        def __repr__(self):
            return f"Key.{self._name}"

    class Key:
        pass

    for name in [
        "caps_lock", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9",
        "f10", "f11", "f12", "esc", "tab", "space", "enter", "backspace",
        "delete", "insert", "home", "end", "page_up", "page_down",
        "up", "down", "left", "right", "num_lock", "scroll_lock", "pause",
        "print_screen",
        "alt_l", "alt_r", "ctrl_l", "ctrl_r", "shift_l", "shift_r", "cmd",
    ]:
        setattr(Key, name, _KeySentinel(name))

    class KeyCode:
        def __init__(self, vk=None, char=None):
            self.vk = vk
            self.char = char

        def __eq__(self, other):
            return isinstance(other, KeyCode) and self.vk == other.vk and self.char == other.char

        def __repr__(self):
            return f"KeyCode(vk={self.vk!r}, char={self.char!r})"

    class Controller:
        def press(self, *a, **k):
            pass

        def release(self, *a, **k):
            pass

    keyboard_mod.Key = Key
    keyboard_mod.KeyCode = KeyCode
    keyboard_mod.Controller = Controller

    class Button:
        left = _KeySentinel("left")
        right = _KeySentinel("right")
        middle = _KeySentinel("middle")

    class MouseController:
        def __init__(self):
            self.events = []

        def click(self, button, *a, **k):
            self.events.append(("click", button))

        def press(self, button, *a, **k):
            self.events.append(("press", button))

        def release(self, button, *a, **k):
            self.events.append(("release", button))

        def scroll(self, *a, **k):
            pass

    mouse_mod.Button = Button
    mouse_mod.Controller = MouseController

    pynput.keyboard = keyboard_mod
    pynput.mouse = mouse_mod
    sys.modules["pynput"] = pynput
    sys.modules["pynput.keyboard"] = keyboard_mod
    sys.modules["pynput.mouse"] = mouse_mod


_install_pynput_stub_if_needed()

from core.config import Config
from core.events import (
    FATAL_CRASH,
    FATAL_PORT_IN_USE,
    ClientCount,
    EventBus,
    Fatal,
    LogLine,
    Ready,
    TelemetryState,
)
from core.status import CONNECTED, ERROR, LISTENING, PORT_IN_USE
from ui.app import DRAIN_LIMIT, BridgeApp, ServerThread


def _ready():
    return Ready(ips=("192.168.1.42", "10.0.0.5"), port=8765, token="abcdef1234567890",
                 cert_fp="A1:B2:C3", pairing_uri="starcompanion://pair?v=2")


@pytest.fixture
def app(tk_root, tmp_path):
    bus = EventBus()
    instance = BridgeApp(Config(), bus, tmp_path, root=tk_root, autostart_server=False)
    yield instance


def test_ready_fills_the_pair_card(app):
    app.bus.emit(_ready())
    app.drain_once()
    assert app.status.status == LISTENING
    assert app.ip_row.value == "192.168.1.42"
    assert app.port_row.value == "8765"
    assert app.token_row.value == "abcdef1234567890"
    assert app.token_row.displayed == "abcd…7890"
    assert app.cert_row.value == "A1:B2:C3"


def test_several_ips_become_a_selector_and_switching_redraws_the_qr(app):
    app.bus.emit(_ready())
    app.drain_once()
    assert app.ip_choices == ["192.168.1.42", "10.0.0.5"]
    first = app.qr_payload
    app.select_ip("10.0.0.5")
    assert app.ip_row.value == "10.0.0.5"
    assert app.qr_payload != first
    assert "10.0.0.5" in app.qr_payload


def test_a_single_ip_offers_no_selector(app):
    app.bus.emit(Ready(ips=("192.168.1.42",), port=8765, token="t",
                       cert_fp="f", pairing_uri="starcompanion://pair"))
    app.drain_once()
    assert app.ip_choices == ["192.168.1.42"]


def test_client_count_reaches_the_header(app):
    app.bus.emit(_ready())
    app.bus.emit(ClientCount(2))
    app.drain_once()
    assert app.status.status == CONNECTED
    assert app.header_label.cget("text") == "Connected — 2 apps"
    assert app.dot.color == "#4ade80"


def test_log_lines_reach_the_activity_pane(app):
    app.bus.emit(LogLine(ts="21:04:11", level=logging.INFO, text="App connected"))
    app.drain_once()
    assert app.activity.lines == ["21:04:11  App connected"]


def test_telemetry_state_reaches_the_strip(app):
    app.bus.emit(TelemetryState(found=True, path="F:/SC/Game.log", feeds=2))
    app.drain_once()
    assert "2 feeds" in app.telemetry_label.cget("text")
    app.bus.emit(TelemetryState(found=False, path=None, feeds=0))
    app.drain_once()
    assert "not found" in app.telemetry_label.cget("text").lower()


def test_dropped_events_are_reported_rather_than_hidden(app):
    small = EventBus(maxsize=1)
    app.bus = small
    small.emit(LogLine(ts="21:04:11", level=logging.INFO, text="kept"))
    small.emit(LogLine(ts="21:04:12", level=logging.INFO, text="dropped"))
    app.drain_once()
    assert any("dropped" in line for line in app.activity.lines)


def test_the_drain_is_bounded_per_tick(app):
    for i in range(DRAIN_LIMIT + 50):
        app.bus.emit(LogLine(ts="21:04:11", level=logging.INFO, text=f"line {i}"))
    app.drain_once()
    assert len(app.activity.lines) == DRAIN_LIMIT


def test_port_in_use_shows_the_failure_row_with_both_remedies(app):
    app.bus.emit(Fatal(kind=FATAL_PORT_IN_USE, message="port 8765 is already in use"))
    app.drain_once()
    assert app.status.status == PORT_IN_USE
    assert app.failure_visible is True
    assert app.retry_button is not None
    assert app.settings_shortcut_visible is True


def test_a_crash_shows_the_failure_row_without_the_settings_shortcut(app):
    app.bus.emit(Fatal(kind=FATAL_CRASH, message="boom"))
    app.drain_once()
    assert app.status.status == ERROR
    assert app.failure_visible is True
    assert app.settings_shortcut_visible is False
    assert "boom" in app.failure_label.cget("text")


def test_the_window_stays_open_after_a_fatal(app):
    app.bus.emit(Fatal(kind=FATAL_CRASH, message="boom"))
    app.drain_once()
    assert app.root.winfo_exists()


def test_retry_clears_the_failure_and_restarts(app, monkeypatch):
    restarts = []
    monkeypatch.setattr(app, "start_server", lambda: restarts.append(1))
    app.bus.emit(Fatal(kind=FATAL_CRASH, message="boom"))
    app.drain_once()
    app.retry()
    assert app.status.fatal is False
    assert app.failure_visible is False
    assert restarts == [1]


def test_restart_keeps_the_token_and_clears_the_card(app, monkeypatch):
    monkeypatch.setattr(app, "start_server", lambda: None)
    monkeypatch.setattr(app, "stop_server", lambda timeout=3.0: True)
    app.bus.emit(_ready())
    app.drain_once()
    app.restart()
    assert app.token_row.value == ""  # cleared until the new Ready arrives
    assert app.status.status == "STARTING"


def test_apply_settings_persists_and_restarts_only_when_the_port_changed(app, monkeypatch):
    restarts = []
    monkeypatch.setattr(app, "restart", lambda: restarts.append(1))
    changed = Config(port=9100, telemetry_enabled=False)
    app.apply_settings(changed)
    from core.config import load

    saved, _ = load(app.app_dir)
    assert saved.port == 9100
    assert saved.telemetry_enabled is False
    assert restarts == [1]

    restarts.clear()
    app.apply_settings(Config(port=9100, telemetry_enabled=True))
    assert restarts == []  # telemetry applies live, no restart needed


def test_server_thread_reports_port_in_use_as_a_fatal_event(tmp_path, monkeypatch):
    import socket

    import bridge_server

    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    bus = EventBus()
    try:
        thread = ServerThread(port=port, bus=bus)
        thread.start()
        thread.join(timeout=15)
        assert not thread.alive
    finally:
        blocker.close()
    fatals = [e for e in bus.drain(limit=1000)[0] if isinstance(e, Fatal)]
    assert fatals and fatals[0].kind == FATAL_PORT_IN_USE


def test_server_thread_emits_ready_and_stops_cleanly(tmp_path, monkeypatch):
    import socket

    import bridge_server

    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    bus = EventBus()
    thread = ServerThread(port=port, bus=bus)
    thread.start()
    ready = None
    for _ in range(150):
        events, _ = bus.drain(limit=100)
        ready = next((e for e in events if isinstance(e, Ready)), ready)
        if ready is not None:
            break
        threading.Event().wait(0.1)
    assert ready is not None, "server never reported Ready"
    assert thread.stop(timeout=10) is True
    assert not thread.alive


def test_server_thread_is_a_daemon_so_a_hung_tailer_cannot_outlive_the_window(tmp_path):
    thread = ServerThread(port=1, bus=EventBus())
    assert thread.daemon is True
