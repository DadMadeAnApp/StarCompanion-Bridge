"""Start-with-Windows, via the per-user Run key.

HKCU rather than HKLM deliberately: it needs no elevation, and a keystroke
bridge has no business asking for admin. Every registry call is best-effort --
a locked-down machine should grey the checkbox out, not crash the panel.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "StarCompanionBridge"


def _winreg():
    """Imported through a function so tests can substitute a fake on macOS."""
    import winreg

    return winreg


def supported() -> bool:
    return sys.platform == "win32" and getattr(sys, "frozen", False)


def reason_unsupported() -> str:
    if sys.platform != "win32":
        return "Start with Windows is only available on Windows."
    return "Only available for the packaged exe — a .py script has no fixed path to register."


def command_for(exe: Path) -> str:
    """The Run value: always quoted, since the exe usually sits under a path with spaces."""
    return f'"{Path(exe)}"'


def read_entry() -> str | None:
    if sys.platform != "win32":
        return None
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return value
    except (FileNotFoundError, OSError):
        return None


def enable(exe: Path) -> None:
    if sys.platform != "win32":
        return
    winreg = _winreg()
    with contextlib.suppress(OSError):
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command_for(exe))


def disable() -> None:
    if sys.platform != "win32":
        return
    winreg = _winreg()
    with contextlib.suppress(FileNotFoundError, OSError):
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)


def is_stale(exe: Path) -> bool:
    """True when an entry exists but points somewhere other than this exe."""
    entry = read_entry()
    if entry is None:
        return False
    return entry.strip().strip('"').casefold() != str(Path(exe)).casefold()
