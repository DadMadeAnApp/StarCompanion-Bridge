import gc
import sys
import types

import pytest


def install_pynput_stub_if_needed() -> None:
    """Stub pynput before anything imports bridge_server.

    bridge_server.py's SPECIAL_KEYS table is built at import time and
    references Key members (e.g. Key.insert) that macOS's real pynput
    backend does not define, so importing it unstubbed raises AttributeError
    before any test can run. tests/test_bridge_server.py carries its own
    copy of this stub (it is a protected file, so it keeps it), guarded by
    `if "pynput" in sys.modules: return`. Any other test module that ends up
    importing bridge_server (directly or via ui.app) needs the same stub in
    place first -- and needs it from here, not a second hand-copied version,
    so there is exactly one place to keep it in sync with bridge_server's key
    table.
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


@pytest.fixture
def tk_root():
    """A hidden Tk root, or a skip when there is no display.

    CI and the macOS dev machine may both lack one; the logic these widgets
    wrap is tested separately so a skip here costs little.
    """
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except Exception as exc:  # noqa: BLE001 - any display failure is a skip
        pytest.skip(f"no display available: {exc}")
    root.withdraw()
    yield root
    # Widgets built on this root (e.g. BooleanVar) commonly sit in reference
    # cycles (bound-method callbacks closing over the widget that owns them),
    # so they are not freed the instant the test's fixtures go out of scope.
    # If root.destroy() runs first, the interpreter is gone by the time the
    # cyclic collector eventually frees them, and Variable.__del__ raises
    # "main thread is not in main loop" as an unraisable exception -- which
    # pytest then blames on whatever unrelated test happens to be running
    # when the collector next fires. Force collection while the interpreter
    # is still alive so that cleanup happens here, deterministically.
    gc.collect()
    root.destroy()
