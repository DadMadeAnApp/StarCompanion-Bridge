import sys

import pytest

from tests.conftest import install_pynput_stub_if_needed

# bridge_server's SPECIAL_KEYS table needs a pynput stub on this platform
# (see conftest.install_pynput_stub_if_needed's docstring). Must run before
# the `import bridge_server` below.
install_pynput_stub_if_needed()

import bridge_server


def test_console_flag_is_recognised(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py", "--console"])
    args = bridge_server._parse_options()
    assert args.console is True


def test_gui_is_the_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py"])
    args = bridge_server._parse_options()
    assert args.console is False


def test_an_unset_port_stays_none_so_the_config_can_supply_it(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py"])
    assert bridge_server._parse_options().port is None


def test_an_explicit_port_is_returned(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py", "--port", "9100"])
    assert bridge_server._parse_options().port == 9100


def test_a_missing_log_path_still_refuses_to_start(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv",
                        ["bridge_server.py", "--log", str(tmp_path / "nope.log")])
    monkeypatch.setattr("builtins.input", lambda *a: "")
    with pytest.raises(SystemExit):
        bridge_server._parse_options()


def test_an_existing_log_path_is_accepted(monkeypatch, tmp_path):
    log = tmp_path / "Game.log"
    log.write_text("x")
    monkeypatch.setattr(sys, "argv", ["bridge_server.py", "--log", str(log)])
    args = bridge_server._parse_options()
    assert args.log_path == str(log)


def test_the_retired_no_auth_optouts_still_refuse(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py", "--insecure-no-auth"])
    monkeypatch.setattr("builtins.input", lambda *a: "")
    with pytest.raises(SystemExit):
        bridge_server._parse_options()


def test_the_env_var_optout_still_refuses(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py"])
    monkeypatch.setenv("STARCOMPANION_INSECURE_NO_AUTH", "1")
    monkeypatch.setattr("builtins.input", lambda *a: "")
    with pytest.raises(SystemExit):
        bridge_server._parse_options()


def test_start_launches_the_gui_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py"])
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    launched = {}
    monkeypatch.setattr("ui.app.run_gui",
                        lambda cfg, app_dir, config_warning=None: launched.update(
                            port=cfg.port, dir=app_dir))
    bridge_server.start()
    assert launched["port"] == 8765
    assert launched["dir"] == tmp_path


def test_start_prefers_the_configured_port_over_the_default(monkeypatch, tmp_path):
    from core.config import Config, save

    save(Config(port=9100), tmp_path)
    monkeypatch.setattr(sys, "argv", ["bridge_server.py"])
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    seen = {}
    monkeypatch.setattr("ui.app.run_gui",
                        lambda cfg, app_dir, config_warning=None: seen.update(port=cfg.port))
    bridge_server.start()
    assert seen["port"] == 9100


def test_a_cli_port_beats_the_configured_one(monkeypatch, tmp_path):
    from core.config import Config, save

    save(Config(port=9100), tmp_path)
    monkeypatch.setattr(sys, "argv", ["bridge_server.py", "--port", "7000"])
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    seen = {}
    monkeypatch.setattr("ui.app.run_gui",
                        lambda cfg, app_dir, config_warning=None: seen.update(
                            port=cfg.port, from_cli=cfg.port_from_cli))
    bridge_server.start()
    assert seen["port"] == 7000
    assert seen["from_cli"] is True


def test_console_mode_does_not_touch_the_gui(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py", "--console"])
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)

    def boom(*a, **k):
        raise AssertionError("console mode must not import or start the GUI")

    monkeypatch.setattr("ui.app.run_gui", boom)
    ran = {}
    monkeypatch.setattr(bridge_server, "run_console", lambda port: ran.update(port=port))
    bridge_server.start()
    assert ran["port"] == 8765
