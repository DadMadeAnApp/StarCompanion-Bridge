import socket

import pytest

from core.config import Config
from ui.settings import validate_log_path, validate_port


def test_a_free_port_validates():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert validate_port(str(free), current_port=8765) is None


def test_a_non_numeric_port_is_rejected():
    assert "number" in validate_port("eight", current_port=8765).lower()


def test_an_empty_port_is_rejected():
    assert validate_port("", current_port=8765) is not None


def test_a_privileged_or_out_of_range_port_is_rejected():
    assert "1024" in validate_port("80", current_port=8765)
    assert validate_port("70000", current_port=8765) is not None
    assert validate_port("-1", current_port=8765) is not None


def test_a_taken_port_is_rejected_before_we_restart_into_a_dead_state():
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    taken = blocker.getsockname()[1]
    try:
        assert "in use" in validate_port(str(taken), current_port=8765).lower()
    finally:
        blocker.close()


def test_the_port_we_are_already_serving_on_is_not_reported_as_in_use():
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    ours = blocker.getsockname()[1]
    try:
        # It is in use *by us*. Rejecting it would make the panel unable to save
        # any unrelated change without also moving the port.
        assert validate_port(str(ours), current_port=ours) is None
    finally:
        blocker.close()


def test_an_empty_log_path_means_auto_detect():
    assert validate_log_path("") is None


def test_a_missing_log_path_is_rejected(tmp_path):
    message = validate_log_path(str(tmp_path / "nope" / "Game.log"))
    assert message is not None and "does not exist" in message


def test_a_directory_is_not_a_log_file(tmp_path):
    assert validate_log_path(str(tmp_path)) is not None


def test_an_existing_readable_log_validates(tmp_path):
    log = tmp_path / "Game.log"
    log.write_text("<2026-08-14T00:00:00.000Z> ok\n")
    assert validate_log_path(str(log)) is None


def test_an_unreadable_log_is_rejected(tmp_path):
    log = tmp_path / "Game.log"
    log.write_text("x")
    log.chmod(0o000)
    try:
        message = validate_log_path(str(log))
    finally:
        log.chmod(0o644)
    if message is None:
        pytest.skip("running as a user that can read mode-000 files")
    assert "cannot be read" in message


def test_the_dialog_returns_the_edited_config(tk_root, tmp_path):
    from ui.settings import SettingsDialog

    saved = []
    dialog = SettingsDialog(tk_root, Config(port=8765), tmp_path, on_save=saved.append)
    dialog.port_var.set("9100")
    dialog.telemetry_var.set(False)
    assert dialog.save() is True
    assert saved and saved[0].port == 9100
    assert saved[0].telemetry_enabled is False


def test_the_dialog_refuses_to_save_an_invalid_port(tk_root, tmp_path):
    from ui.settings import SettingsDialog

    saved = []
    dialog = SettingsDialog(tk_root, Config(), tmp_path, on_save=saved.append)
    dialog.port_var.set("80")
    assert dialog.save() is False
    assert saved == []
    assert "1024" in dialog.error_label.cget("text")


def test_cancel_saves_nothing(tk_root, tmp_path):
    from ui.settings import SettingsDialog

    saved = []
    dialog = SettingsDialog(tk_root, Config(port=8765), tmp_path, on_save=saved.append)
    dialog.port_var.set("9100")
    dialog.cancel()
    assert saved == []


def test_a_cli_supplied_port_is_shown_but_not_editable(tk_root, tmp_path):
    from ui.settings import SettingsDialog

    cfg = Config(port=7000, port_from_cli=True)
    dialog = SettingsDialog(tk_root, cfg, tmp_path, on_save=lambda c: None)
    assert str(dialog.port_spin.cget("state")) == "disabled"
    assert "--port" in dialog.port_note.cget("text")


def test_a_cli_supplied_log_path_is_shown_but_not_editable(tk_root, tmp_path):
    from ui.settings import SettingsDialog

    cfg = Config(log_path="D:/x/Game.log", log_from_cli=True)
    dialog = SettingsDialog(tk_root, cfg, tmp_path, on_save=lambda c: None)
    assert str(dialog.log_entry.cget("state")) == "disabled"
    assert "--log" in dialog.log_note.cget("text")


def test_autostart_is_disabled_with_a_reason_when_unsupported(tk_root, tmp_path, monkeypatch):
    import sys

    from ui.settings import SettingsDialog

    monkeypatch.setattr(sys, "platform", "darwin")
    dialog = SettingsDialog(tk_root, Config(), tmp_path, on_save=lambda c: None)
    assert str(dialog.autostart_check.cget("state")) == "disabled"
