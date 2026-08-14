import json

from core.config import CONFIG_NAME, CONFIG_VERSION, Config, apply_cli, load, save


def test_load_with_no_file_returns_defaults_and_no_warning(tmp_path):
    cfg, warning = load(tmp_path)
    assert cfg.port == 8765
    assert cfg.log_path is None
    assert cfg.telemetry_enabled is True
    assert cfg.start_with_windows is False
    assert cfg.window_height == 620
    assert warning is None


def test_save_then_load_round_trips(tmp_path):
    cfg = Config(port=9000, log_path="F:/SC/Game.log", telemetry_enabled=False,
                 start_with_windows=True, window_height=800)
    save(cfg, tmp_path)
    loaded, warning = load(tmp_path)
    assert warning is None
    assert loaded.port == 9000
    assert loaded.log_path == "F:/SC/Game.log"
    assert loaded.telemetry_enabled is False
    assert loaded.start_with_windows is True
    assert loaded.window_height == 800


def test_saved_file_records_its_version(tmp_path):
    save(Config(), tmp_path)
    written = json.loads((tmp_path / CONFIG_NAME).read_text())
    assert written["version"] == CONFIG_VERSION
    assert written["window"] == {"height": 620}


def test_unknown_keys_survive_a_save(tmp_path):
    (tmp_path / CONFIG_NAME).write_text(json.dumps(
        {"version": 1, "port": 8765, "future_setting": "keep me"}
    ))
    cfg, _ = load(tmp_path)
    assert cfg.extras["future_setting"] == "keep me"
    save(cfg, tmp_path)
    written = json.loads((tmp_path / CONFIG_NAME).read_text())
    assert written["future_setting"] == "keep me"
    assert written["port"] == 8765


def test_corrupt_file_is_quarantined_and_defaults_are_used(tmp_path):
    (tmp_path / CONFIG_NAME).write_text("{not json at all")
    cfg, warning = load(tmp_path)
    assert cfg.port == 8765
    assert warning is not None and "bridge_config.json.bad" in warning
    assert (tmp_path / "bridge_config.json.bad").exists()
    assert not (tmp_path / CONFIG_NAME).exists()


def test_a_json_file_that_is_not_an_object_is_also_quarantined(tmp_path):
    (tmp_path / CONFIG_NAME).write_text("[1, 2, 3]")
    cfg, warning = load(tmp_path)
    assert cfg.port == 8765
    assert warning is not None


def test_wrong_typed_values_fall_back_per_field(tmp_path):
    (tmp_path / CONFIG_NAME).write_text(json.dumps(
        {"version": 1, "port": "not a number", "telemetry_enabled": "yes"}
    ))
    cfg, warning = load(tmp_path)
    assert cfg.port == 8765
    assert cfg.telemetry_enabled is True
    assert warning is not None


def test_bool_window_height_falls_back_to_default(tmp_path):
    (tmp_path / CONFIG_NAME).write_text(json.dumps(
        {"version": 1, "window": {"height": True}}
    ))
    cfg, warning = load(tmp_path)
    assert cfg.window_height == 620
    assert warning is not None


def test_cli_flags_win_over_the_config_file(tmp_path):
    cfg = Config(port=9000, log_path="F:/from/config/Game.log")
    merged = apply_cli(cfg, cli_port=7000, cli_log="D:/from/cli/Game.log")
    assert merged.port == 7000
    assert merged.log_path == "D:/from/cli/Game.log"
    assert merged.port_from_cli is True
    assert merged.log_from_cli is True


def test_absent_cli_flags_leave_the_config_alone(tmp_path):
    cfg = Config(port=9000, log_path="F:/from/config/Game.log")
    merged = apply_cli(cfg, cli_port=None, cli_log=None)
    assert merged.port == 9000
    assert merged.log_path == "F:/from/config/Game.log"
    assert merged.port_from_cli is False
    assert merged.log_from_cli is False


def test_cli_provenance_is_never_written_to_disk(tmp_path):
    cfg = apply_cli(Config(), cli_port=7000, cli_log=None)
    save(cfg, tmp_path)
    written = json.loads((tmp_path / CONFIG_NAME).read_text())
    assert "port_from_cli" not in written
    assert "log_from_cli" not in written
