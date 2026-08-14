import logging
import threading

import pytest

from tests.conftest import install_pynput_stub_if_needed

# ui.app imports bridge_server, whose SPECIAL_KEYS table needs a pynput stub
# on this platform (see conftest.install_pynput_stub_if_needed's docstring).
# Must run before the `from ui.app import ...` below.
install_pynput_stub_if_needed()

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


def test_restart_starts_no_second_thread_when_the_old_one_wont_stop(app, monkeypatch):
    # If the old ServerThread doesn't unwind in time (e.g. a tailer blocked on
    # a file the game has locked), starting a replacement would leave two
    # threads fighting over the same port -- the new one dies with
    # EADDRINUSE while the old one keeps running and emitting to the bus.
    starts = []
    monkeypatch.setattr(app, "start_server", lambda: starts.append(1))
    monkeypatch.setattr(app, "stop_server", lambda timeout=3.0: False)
    app.restart()
    assert starts == []
    assert app.failure_visible is True
    assert "hasn't released the port" in app.failure_label.cget("text")


def test_retry_stops_the_old_server_first_and_wont_double_start(app, monkeypatch):
    starts = []
    monkeypatch.setattr(app, "start_server", lambda: starts.append(1))
    monkeypatch.setattr(app, "stop_server", lambda timeout=3.0: False)
    app.bus.emit(Fatal(kind=FATAL_CRASH, message="boom"))
    app.drain_once()
    app.retry()
    assert starts == []
    assert app.failure_visible is True
    assert "hasn't released the port" in app.failure_label.cget("text")


def test_restart_keeps_the_token_and_clears_the_card(app, monkeypatch):
    monkeypatch.setattr(app, "start_server", lambda: None)
    monkeypatch.setattr(app, "stop_server", lambda timeout=3.0: True)
    app.bus.emit(_ready())
    app.drain_once()
    app.restart()
    assert app.token_row.value == ""  # cleared until the new Ready arrives
    assert app.status.status == "STARTING"


def test_apply_settings_persists_and_restarts_on_port_or_telemetry_change(app, monkeypatch):
    # A telemetry toggle needs the same restart as a port change: each
    # connection builds its own LogTailer at accept time, so a currently
    # connected phone keeps its old feed state until the listener itself is
    # replaced -- there is no way to apply this one live.
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
    assert restarts == [1]  # telemetry-only change still restarts

    restarts.clear()
    app.apply_settings(Config(port=9100, telemetry_enabled=True))
    assert restarts == []  # no change at all: no restart


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


def test_a_never_started_thread_can_still_be_stopped_harmlessly():
    thread = ServerThread(port=1, bus=EventBus())
    assert thread.stop(timeout=1) is True


def test_a_failing_event_handler_is_isolated_and_does_not_stop_the_batch(app, monkeypatch):
    real_handle = app._handle

    def flaky_handle(event):
        if isinstance(event, Fatal):
            raise RuntimeError("boom while handling Fatal")
        real_handle(event)

    monkeypatch.setattr(app, "_handle", flaky_handle)
    app.bus.emit(Fatal(kind=FATAL_CRASH, message="ignored"))
    app.bus.emit(LogLine(ts="21:04:11", level=logging.INFO, text="still processed"))

    app.drain_once()  # must not raise despite the first event's handler blowing up

    assert any("still processed" in line for line in app.activity.lines)
    assert any("failed to handle" in line for line in app.activity.lines)


def test_a_failing_ready_handler_never_leaks_the_token_to_log_or_activity(app, monkeypatch, caplog):
    """Regression for the finding against the fault-isolation fix: a handler
    exception while processing Ready must not put the pairing token in
    bridge.log (which the README tells users to attach to bug reports) or
    in the on-screen activity pane."""
    sentinel_token = "SENTINEL-TOKEN-DO-NOT-LEAK-13a9f2"
    real_handle = app._handle

    def flaky_handle(event):
        if isinstance(event, Ready):
            raise RuntimeError(f"boom while handling token {sentinel_token}")
        real_handle(event)

    monkeypatch.setattr(app, "_handle", flaky_handle)
    caplog.set_level(logging.ERROR)

    app.bus.emit(Ready(ips=("192.168.1.42",), port=8765, token=sentinel_token,
                       cert_fp="A1:B2:C3",
                       pairing_uri=f"starcompanion://pair?t={sentinel_token}"))
    app.drain_once()

    for record in caplog.records:
        assert sentinel_token not in record.getMessage()
    for line in app.activity.lines:
        assert sentinel_token not in line
    # The failure must still be visible, just without the secret in it.
    assert any("failed to handle" in line for line in app.activity.lines)


def test_the_drain_loop_reschedules_itself_even_if_a_tick_raises(app, monkeypatch):
    scheduled = []
    monkeypatch.setattr(app.root, "after",
                        lambda ms, fn: scheduled.append((ms, fn)))
    monkeypatch.setattr(app, "drain_once",
                        lambda: (_ for _ in ()).throw(RuntimeError("tick blew up")))

    # Tk's own callback dispatcher is what actually swallows an exception
    # raised inside a `root.after` callback in real use; here we only need to
    # prove the `finally` re-armed the poll before that exception propagates.
    with pytest.raises(RuntimeError, match="tick blew up"):
        app._tick()

    assert len(scheduled) == 1
    from ui.app import DRAIN_INTERVAL_MS

    assert scheduled[0][0] == DRAIN_INTERVAL_MS
    assert scheduled[0][1] == app._tick


def test_on_close_destroys_the_window_even_if_save_fails(app, monkeypatch):
    # An unwritable app_dir (e.g. an install under C:\Program Files) makes
    # save() raise/return False. Tk's WM_DELETE_WINDOW dispatcher swallows an
    # exception raised here to a stream that's None in a --windowed build,
    # which would silently skip destroy() -- leaving the window (and the
    # daemon server thread still holding the port) stuck open with no way to
    # close it. destroy() must run regardless. (Spy on destroy rather than
    # really destroying `app.root` -- the tk_root fixture destroys it again
    # on teardown, and a real double-destroy raises TclError.)
    def boom_save(cfg, app_dir):
        raise OSError("read-only file system")

    monkeypatch.setattr("ui.app.save", boom_save)
    monkeypatch.setattr(app, "stop_server", lambda timeout=3.0: True)
    destroyed = []
    monkeypatch.setattr(app.root, "destroy", lambda: destroyed.append(1))
    app.on_close()
    assert destroyed == [1]


def test_on_close_destroys_the_window_even_if_the_server_wont_stop(app, monkeypatch):
    monkeypatch.setattr("ui.app.save", lambda cfg, app_dir: True)
    monkeypatch.setattr(app, "stop_server", lambda timeout=3.0: False)
    destroyed = []
    monkeypatch.setattr(app.root, "destroy", lambda: destroyed.append(1))
    app.on_close()
    assert destroyed == [1]
