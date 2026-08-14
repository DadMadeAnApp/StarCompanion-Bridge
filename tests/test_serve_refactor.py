import asyncio
import errno
import logging
import sys

import pytest

# When the whole suite runs together, test_bridge_server.py (collected first,
# alphabetically) replaces websockets/cryptography/qrcode/pynput in
# sys.modules with bare stand-ins before its own `import bridge_server` — by
# design, per its own docstring, so it never touches the network or a real
# keyboard. That import caches a "bridge_server" module wired to those
# stand-ins. Python's import system then hands the *same* cached module back
# to every later `import bridge_server`, this file's included — but this file
# exercises serve() against a real socket, so it needs the genuine packages.
# Dropping any stand-in (recognised by having no __file__, unlike a real
# installed package) and the cached bridge_server module forces a fresh,
# unstubbed import here, without touching test_bridge_server.py.
for _name in ("websockets", "cryptography", "qrcode", "pynput", "pynput.keyboard", "pynput.mouse"):
    _mod = sys.modules.get(_name)
    if _mod is not None and getattr(_mod, "__file__", None) is None:
        del sys.modules[_name]
sys.modules.pop("bridge_server", None)

# pynput's macOS backend doesn't define every Key member the Windows backend
# does (no Key.insert, Key.num_lock, Key.scroll_lock, Key.print_screen,
# Key.pause). bridge_server's SPECIAL_KEYS dict references all of these at
# *module import time*, so importing it on macOS raises AttributeError before
# this file's tests can even collect. Patching in harmless placeholders for
# just the missing names (leaving everything else real) sidesteps that
# without touching bridge_server.py.
from pynput.keyboard import Key as _Key

for _name in ("insert", "num_lock", "scroll_lock", "print_screen", "pause"):
    if not hasattr(_Key, _name):
        setattr(_Key, _name, object())

import bridge_server


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_serve_reports_everything_needed_to_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    seen = {}
    stop = None

    async def run():
        nonlocal stop
        stop = asyncio.Event()

        def on_ready(info):
            seen.update(info)
            stop.set()

        await bridge_server.serve(port=_free_port(), on_ready=on_ready, stop=stop)

    asyncio.run(run())
    assert set(seen) == {"ips", "port", "token", "cert_fp", "pairing_uri"}
    assert seen["token"]
    assert seen["pairing_uri"].startswith("starcompanion://pair")
    assert isinstance(seen["ips"], list) and seen["ips"]


def test_serve_returns_when_stop_is_set(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(
            bridge_server.serve(port=_free_port(), on_ready=lambda info: None, stop=stop)
        )
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(run())


def test_serve_makes_no_bare_print_calls(tmp_path, monkeypatch, capsys):
    # Without configure_console_logging(), the root logger has no handler, so
    # this only proves serve() itself never calls print() directly — it does
    # not prove anything about what logging.info() produces once a handler is
    # attached. See test_console_mode_setup_notices_get_timestamps_but_the_banner_does_not
    # for that.
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)

    async def run():
        stop = asyncio.Event()
        await bridge_server.serve(
            port=_free_port(), on_ready=lambda info: stop.set(), stop=stop
        )

    asyncio.run(run())
    assert capsys.readouterr().out == ""


def test_console_mode_setup_notices_get_timestamps_but_the_banner_does_not(
    tmp_path, monkeypatch, capsys
):
    # Pins the ruling that narrowed the "byte-identical --console output"
    # constraint: the startup banner/QR/pairing details stay print-only and
    # unchanged, but the cert-setup notices go through logging (so the GUI can
    # show them) and therefore carry the standard timestamp prefix in console
    # mode.
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        # basicConfig() is a no-op if the root logger already has a handler —
        # pytest's own logging capture installs one — so clear it first to get
        # a real handler installed for this test.
        root.handlers[:] = []
        bridge_server.configure_console_logging()

        async def run():
            stop = asyncio.Event()
            await bridge_server.serve(
                port=_free_port(), on_ready=lambda info: stop.set(), stop=stop
            )

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "[setup] Generating TLS certificate" in out
        assert "[setup] Certificate saved to" in out
        # "%H:%M:%S" timestamp prefix, e.g. "14:03:07  [setup] ..."
        import re

        assert re.search(r"\d{2}:\d{2}:\d{2}  \[setup\] Generating TLS certificate", out)
        assert "StarCompanion Desktop Bridge" not in out
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_a_second_serve_call_with_no_on_clients_clears_the_previous_callback(
    tmp_path, monkeypatch
):
    # Task 9's GUI restart calls serve() repeatedly in one process. If
    # on_clients=None left the old callback installed, a restart without a
    # fresh callback would keep firing into a dead consumer.
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    seen = []

    async def run():
        stop1 = asyncio.Event()
        await bridge_server.serve(
            port=_free_port(),
            on_ready=lambda info: stop1.set(),
            on_clients=seen.append,
            stop=stop1,
        )
        stop2 = asyncio.Event()
        await bridge_server.serve(
            port=_free_port(),
            on_ready=lambda info: stop2.set(),
            on_clients=None,
            stop=stop2,
        )
        assert bridge_server._on_clients is None

    asyncio.run(run())


def test_a_taken_port_raises_and_maps_to_port_in_use(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        with pytest.raises(OSError) as caught:
            asyncio.run(bridge_server.serve(port=port, on_ready=lambda info: None))
        assert bridge_server.fatal_kind_for(caught.value) == "PORT_IN_USE"
    finally:
        blocker.close()


def test_fatal_kind_recognises_every_address_in_use_shape():
    for code in (10048, 48, 98):
        assert bridge_server.fatal_kind_for(OSError(code, "in use")) == "PORT_IN_USE"
    windows_style = OSError("in use")
    windows_style.winerror = 10048
    assert bridge_server.fatal_kind_for(windows_style) == "PORT_IN_USE"
    assert bridge_server.fatal_kind_for(OSError(errno.EACCES, "denied")) == "CRASH"
    assert bridge_server.fatal_kind_for(RuntimeError("boom")) == "CRASH"


def test_console_banner_prints_the_pairing_details(capsys):
    bridge_server.console_banner({
        "ips": ["192.168.1.42"],
        "port": 8765,
        "token": "tok123",
        "cert_fp": "A1:B2:C3",
        "pairing_uri": "starcompanion://pair?v=2",
    })
    out = capsys.readouterr().out
    assert "StarCompanion Desktop Bridge" in out
    assert "192.168.1.42" in out
    assert "8765" in out
    assert "tok123" in out
    assert "A1:B2:C3" in out


def test_console_banner_lists_every_ip_when_there_are_several(capsys):
    bridge_server.console_banner({
        "ips": ["192.168.1.42", "10.0.0.5"],
        "port": 8765, "token": "t", "cert_fp": "f",
        "pairing_uri": "starcompanion://pair",
    })
    out = capsys.readouterr().out
    assert "192.168.1.42" in out and "10.0.0.5" in out


def test_client_count_is_reported_on_connect_and_disconnect(monkeypatch):
    counts = []
    monkeypatch.setattr(bridge_server, "_on_clients", counts.append)
    bridge_server._connections.clear()
    try:
        bridge_server._track_connection("ws-a")
        bridge_server._track_connection("ws-b")
        bridge_server._untrack_connection("ws-a")
        assert counts == [1, 2, 1]
    finally:
        bridge_server._connections.clear()


def test_telemetry_master_switch_defaults_on_and_toggles():
    try:
        assert bridge_server.telemetry_enabled() is True
        bridge_server.set_telemetry_enabled(False)
        assert bridge_server.telemetry_enabled() is False
    finally:
        bridge_server.set_telemetry_enabled(True)


def test_a_disabled_master_switch_makes_subscribe_fail_cleanly(monkeypatch):
    # With telemetry off the handler must behave exactly as it does when no log
    # is found, so the app shows its existing distinct state rather than hanging
    # on a subscription that will never produce frames.
    monkeypatch.setattr(bridge_server, "_log_path_override", None)
    bridge_server.set_telemetry_enabled(False)
    try:
        assert bridge_server._resolve_log_path() is None
    finally:
        bridge_server.set_telemetry_enabled(True)
