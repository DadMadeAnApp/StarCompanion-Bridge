"""Tests for bridge_server.py's key-name resolution.

Run: python3 -m unittest test_bridge_server -v

Stubs pynput (and bridge_server's other bootstrap deps) before importing
bridge_server. Two independent reasons for the pynput stub specifically:

1. On a dev machine without pynput installed at all, importing the real
   module trips bridge_server's own dependency-bootstrap code (it tries to
   pip install on import), which is not something a unit test should do.
2. Even where pynput IS installed, its macOS backend does not define every
   Key member the Windows backend does (no Key.insert, Key.num_lock,
   Key.scroll_lock, Key.print_screen, ...). bridge_server's SPECIAL_KEYS
   dict is built at *module import time* and references all of these, so
   importing the real pynput on macOS raises AttributeError before any
   test can even run. Stubbing sidesteps both problems and keeps the test
   platform-independent, matching what bridge_server itself is: pure
   name/VK-table logic that never has to touch a real keyboard to be
   tested.

The stub's Key must define the sided shift_l/shift_r (etc.) names, or the
module fails to import for the same reason as above.
"""
import sys
import types
import unittest


def _install_bootstrap_deps_stub():
    """Fake sys.modules entries for every package bridge_server.py bootstraps.

    bridge_server.py's `_bootstrap()` runs at import time and pip-installs
    websockets/pynput/cryptography/qrcode if any is missing, then re-execs
    the interpreter. Right for the shipped app, wrong for a test: it must
    not reach the network, must not re-exec, and must not require any
    package actually being installed. `_bootstrap` decides "missing" via a
    bare `__import__(name)`, checking all four names regardless of whether
    bridge_server itself imports each at module scope (only `websockets` and
    `pynput` are), so all four need a stub or bootstrap fires.
    """
    for name in ("websockets", "cryptography", "qrcode"):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)


def _install_pynput_stub():
    if "pynput" in sys.modules:
        return

    pynput = types.ModuleType("pynput")
    keyboard_mod = types.ModuleType("pynput.keyboard")
    mouse_mod = types.ModuleType("pynput.mouse")

    class _KeySentinel:
        """Stand-in for a pynput.keyboard.Key enum member."""
        def __init__(self, name):
            self._name = name

        def __repr__(self):
            return f"Key.{self._name}"

    class Key:
        pass

    # Every name bridge_server.py's SPECIAL_KEYS / MODIFIER_KEYS reference.
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
        def click(self, *a, **k):
            pass

        def scroll(self, *a, **k):
            pass

    mouse_mod.Button = Button
    mouse_mod.Controller = MouseController

    pynput.keyboard = keyboard_mod
    pynput.mouse = mouse_mod
    sys.modules["pynput"] = pynput
    sys.modules["pynput.keyboard"] = keyboard_mod
    sys.modules["pynput.mouse"] = mouse_mod


_install_bootstrap_deps_stub()
_install_pynput_stub()

import bridge_server  # noqa: E402  (import must follow the stub install)


class NumpadKeyResolutionTests(unittest.TestCase):
    """VK_NUMPAD0 == 0x60, VK_ADD == 0x6B, VK_SUBTRACT == 0x6D."""

    def test_numpad_names_resolve_to_numpad_vks(self):
        key, _mods = bridge_server._resolve("NUM0")
        self.assertEqual(key.vk, 0x60)

        key, _mods = bridge_server._resolve("NUM9")
        self.assertEqual(key.vk, 0x69)

        key, _mods = bridge_server._resolve("NUMPLUS")
        self.assertEqual(key.vk, 0x6B)

        key, _mods = bridge_server._resolve("NUMMINUS")
        self.assertEqual(key.vk, 0x6D)

    def test_numpad_is_not_the_main_keyboard_row(self):
        # The whole point: without the explicit table, these would fall
        # through to VkKeyScanW / the OEM table and hit the main row.
        key, _mods = bridge_server._resolve("NUM0")
        self.assertNotEqual(key.vk, ord("0"))

        key, _mods = bridge_server._resolve("NUMPLUS")
        self.assertNotEqual(key.vk, ord("+"))

    def test_numpad_works_with_a_modifier_combo(self):
        # SC's Advanced Camera Controls default bindings are RAlt+Numpad n.
        key, mods = bridge_server._resolve("RALT+NUM3")
        self.assertEqual(key.vk, 0x63)
        self.assertEqual(mods, [bridge_server.MODIFIER_KEYS["RALT"]])

    def test_all_documented_numpad_names_resolve(self):
        expected = {
            "NUM0": 0x60, "NUM1": 0x61, "NUM2": 0x62, "NUM3": 0x63,
            "NUM4": 0x64, "NUM5": 0x65, "NUM6": 0x66, "NUM7": 0x67,
            "NUM8": 0x68, "NUM9": 0x69, "NUMSTAR": 0x6A, "NUMPLUS": 0x6B,
            "NUMENTER": 0x0D, "NUMMINUS": 0x6D, "NUMPERIOD": 0x6E,
            "NUMSLASH": 0x6F,
        }
        for name, vk in expected.items():
            with self.subTest(name=name):
                key, _mods = bridge_server._resolve(name)
                self.assertEqual(key.vk, vk)

    def test_unknown_key_still_returns_none(self):
        key, mods = bridge_server._resolve("NOTAREALKEY")
        self.assertIsNone(key)
        self.assertEqual(mods, [])


if __name__ == "__main__":
    unittest.main()
