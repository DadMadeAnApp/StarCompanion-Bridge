import sys
from pathlib import Path

import pytest

from core import autostart


class FakeRegistry:
    """Stand-in for winreg, so this runs on macOS."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.deleted = []

    class _Key:
        def __init__(self, registry):
            self.registry = registry

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, path, reserved=0, access=None):  # noqa: N802
        return self._Key(self)

    def CreateKeyEx(self, root, path, reserved=0, access=None):  # noqa: N802
        return self._Key(self)

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]
        self.deleted.append(name)


@pytest.fixture
def registry(monkeypatch):
    fake = FakeRegistry()
    monkeypatch.setattr(autostart, "_winreg", lambda: fake)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    return fake


def test_unsupported_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert autostart.supported() is False
    assert "Windows" in autostart.reason_unsupported()


def test_unsupported_when_running_as_a_script(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert autostart.supported() is False
    # There is no stable path to register for a .py invocation.
    assert "exe" in autostart.reason_unsupported()


def test_supported_when_frozen_on_windows(registry):
    assert autostart.supported() is True


def test_the_command_is_quoted_for_paths_with_spaces():
    got = autostart.command_for(Path(r"C:\Program Files\SC\StarCompanionBridge.exe"))
    assert got == r'"C:\Program Files\SC\StarCompanionBridge.exe"'


def test_enable_writes_the_run_value(registry):
    exe = Path(r"C:\apps\StarCompanionBridge.exe")
    autostart.enable(exe)
    assert registry.values["StarCompanionBridge"] == r'"C:\apps\StarCompanionBridge.exe"'


def test_read_entry_returns_none_when_absent(registry):
    assert autostart.read_entry() is None


def test_read_entry_returns_the_written_value(registry):
    autostart.enable(Path(r"C:\apps\StarCompanionBridge.exe"))
    assert autostart.read_entry() == r'"C:\apps\StarCompanionBridge.exe"'


def test_disable_removes_the_value(registry):
    autostart.enable(Path(r"C:\apps\StarCompanionBridge.exe"))
    autostart.disable()
    assert "StarCompanionBridge" not in registry.values
    assert autostart.read_entry() is None


def test_disable_is_harmless_when_there_is_nothing_to_remove(registry):
    autostart.disable()  # must not raise
    assert autostart.read_entry() is None


def test_a_moved_exe_makes_the_entry_stale(registry):
    autostart.enable(Path(r"C:\old\StarCompanionBridge.exe"))
    assert autostart.is_stale(Path(r"C:\new\StarCompanionBridge.exe")) is True
    assert autostart.is_stale(Path(r"C:\old\StarCompanionBridge.exe")) is False


def test_no_entry_is_not_stale(registry):
    assert autostart.is_stale(Path(r"C:\apps\StarCompanionBridge.exe")) is False


def test_a_registry_error_is_swallowed_rather_than_crashing_the_settings_panel(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    class Broken(FakeRegistry):
        def CreateKeyEx(self, *a, **k):  # noqa: N802
            raise OSError("access denied")

        def OpenKey(self, *a, **k):  # noqa: N802
            raise OSError("access denied")

    monkeypatch.setattr(autostart, "_winreg", lambda: Broken())
    autostart.enable(Path(r"C:\apps\x.exe"))  # must not raise
    assert autostart.read_entry() is None
