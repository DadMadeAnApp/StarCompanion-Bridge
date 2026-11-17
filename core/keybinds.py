"""Keybind export: the user's effective keyboard bindings, in the bridge's key syntax.

Two sources are merged on every request (read fresh -- the user's file changes
whenever they rebind in game):

- defaults: ``Data/Libs/Config/defaultProfile.xml`` inside the user's Data.p4k;
- user deltas: ``<LIVE>/user/client/0/Profiles/<profile>/actionmaps.xml``.

Only keyboard inputs are exported, and each is translated from Star Citizen's
input syntax (``kb1_lalt+n``, ``np_1``, ``f1``) into the bridge's own ``MOD+KEY``
syntax (HANDOFF §7). The *bridge's* key resolver is the source of truth for what
is expressible: the translation proposes a string and a caller-supplied
``is_valid`` predicate (bridge_server's ``_resolve``) disposes. Anything it
rejects is counted in ``skipped`` and omitted.

LEGAL BOUNDARY: the p4k-derived defaults live in memory only; individual
bindings are sent, never the file.

UNVERIFIED on a real install: the shape of defaultProfile.xml (assumed
``<actionmap name><action name keyboard="...">``, with ``<rebind input="kb1_...">``
children also accepted) and the SC key-name spellings in `_SC_KEYS`.
"""

from __future__ import annotations

import logging
import re
import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

from core import p4k

EXPORT_VERSION = 1
# Stay under the 1 MiB default inbound limit of common WebSocket clients
# (iOS URLSessionWebSocketTask). HANDOFF §8 only limits app->bridge frames.
MAX_ACK_BYTES = 900_000

_KB_PREFIX = re.compile(r"^kb\d*_", re.IGNORECASE)
_OTHER_PREFIX = re.compile(r"^(js|gp|mo|mouse|joystick|gamepad)\d*_", re.IGNORECASE)

# SC modifier names -> bridge modifier names (HANDOFF §7).
_SC_MODS = {
    "lalt": "LALT", "ralt": "RALT", "alt": "ALT",
    "lctrl": "LCTRL", "rctrl": "RCTRL", "ctrl": "CTRL",
    "lshift": "LSHIFT", "rshift": "RSHIFT", "shift": "SHIFT",
    "lwin": "WIN", "rwin": "WIN",
}

# SC key names that differ from the bridge's. Everything else (single letters,
# digits, f1..f12, up/down/left/right, home, end, insert, delete, space, tab,
# pause) is the same word upper-cased. UNVERIFIED spellings -- an unknown name
# simply fails `is_valid` and is counted as skipped.
_SC_KEYS = {
    "escape": "ESC", "enter": "ENTER", "return": "ENTER", "backspace": "BACKSPACE",
    "pgup": "PAGEUP", "pgdn": "PAGEDOWN", "pageup": "PAGEUP", "pagedown": "PAGEDOWN",
    "capslock": "CAPS", "numlock": "NUM_LOCK", "scrolllock": "SCROLL_LOCK",
    "print": "PRINT_SCREEN", "printscreen": "PRINT_SCREEN",
    "minus": "-", "equals": "=", "comma": ",", "period": ".", "slash": "/",
    "backslash": "\\", "semicolon": ";", "apostrophe": "'",
    "lbracket": "[", "rbracket": "]", "grave": "`",
    # Numeric keypad.
    "np_add": "NUMPLUS", "np_subtract": "NUMMINUS", "np_multiply": "NUMSTAR",
    "np_divide": "NUMSLASH", "np_period": "NUMPERIOD", "np_enter": "NUMENTER",
    **{f"np_{n}": f"NUM{n}" for n in range(10)},
}


def _is_blank(token: str) -> bool:
    return token.strip() == ""


def keyboard_token(raw: str) -> str | None:
    """Classify one SC input.

    Returns the input with its keyboard prefix removed (possibly "" for a
    cleared binding), or None when it is a non-keyboard device (dropped, not
    counted as skipped). Unprefixed inputs are treated as keyboard, which is how
    defaultProfile.xml's ``keyboard=`` attribute is assumed to spell them.
    """
    if _KB_PREFIX.match(raw):
        return _KB_PREFIX.sub("", raw, count=1)
    if _OTHER_PREFIX.match(raw):
        return None
    return raw


def translate(sc_input: str, is_valid: Callable[[str], bool]) -> str | None:
    """SC keyboard input (prefix already removed) -> bridge key string, or None."""
    text = sc_input.strip()
    if not text:
        return None
    parts = text.split("+")
    if any(p == "" for p in parts):
        return None                      # "+" itself or a ragged combo: not expressible
    out: list[str] = []
    for i, part in enumerate(parts):
        word = part.strip().lower()
        last = i == len(parts) - 1
        if not last:
            mod = _SC_MODS.get(word)
            if mod is None:
                return None
            out.append(mod)
            continue
        if word in _SC_KEYS:
            out.append(_SC_KEYS[word])
        elif len(word) == 1 or re.fullmatch(r"f\d{1,2}", word) or word in {
            "up", "down", "left", "right", "home", "end", "insert", "delete",
            "space", "tab", "pause",
        }:
            out.append(word.upper())
        else:
            return None
    result = "+".join(out)
    return result if is_valid(result) else None


# ── Reading the two sources ───────────────────────────────────────────────────
def _keyboard_inputs(action: ET.Element) -> list[str] | None:
    """Keyboard inputs an <action> declares, prefix stripped; None if it has none.

    A cleared binding appears as "" in the returned list.
    """
    found: list[str] = []
    attr = action.get("keyboard")
    if attr is not None:
        stripped = keyboard_token(attr)
        if stripped is not None:
            found.append(stripped)
    for rebind in action.iter("rebind"):
        raw = rebind.get("input")
        if raw is None:
            continue
        stripped = keyboard_token(raw)
        if stripped is not None:
            found.append(stripped)
    return found or None


def _collect(root: ET.Element) -> dict[tuple[str, str], list[str]]:
    result: dict[tuple[str, str], list[str]] = {}
    for amap in root.iter("actionmap"):
        map_name = amap.get("name")
        if not map_name:
            continue
        for action in amap.findall("action"):
            name = action.get("name")
            if not name:
                continue
            inputs = _keyboard_inputs(action)
            if inputs is None:
                continue
            result.setdefault((map_name, name), []).extend(inputs)
    return result


def ci_child(parent: Path, name: str) -> Path | None:
    """Child of `parent` matching `name` case-insensitively (exact match first)."""
    exact = parent / name
    if exact.exists():
        return exact
    try:
        for child in parent.iterdir():
            if child.name.casefold() == name.casefold():
                return child
    except OSError:
        pass
    return None


def find_user_actionmaps(live_dir: Path) -> tuple[Path, str] | None:
    """The actionmaps.xml to read and its profile name, or None.

    Prefers a profile called "default"; otherwise the most recently modified.
    """
    node: Path | None = live_dir
    for part in ("user", "client", "0", "Profiles"):
        node = ci_child(node, part) if node else None
    if node is None or not node.is_dir():
        return None
    candidates: list[tuple[float, str, Path]] = []
    try:
        for profile in node.iterdir():
            if not profile.is_dir():
                continue
            xml = ci_child(profile, "actionmaps.xml")
            if xml is not None and xml.is_file():
                try:
                    candidates.append((xml.stat().st_mtime, profile.name, xml))
                except OSError:
                    continue
    except OSError:
        return None
    if not candidates:
        return None
    for _mtime, name, xml in candidates:
        if name.casefold() == "default":
            return xml, name
    _mtime, name, xml = max(candidates, key=lambda c: c[0])
    return xml, name


_defaults_lock = threading.Lock()
_defaults_cache: dict[Path, tuple[tuple[int, int], dict]] = {}

MAX_ACTIONMAPS_BYTES = 8 * 1024 * 1024


def load_defaults(p4k_path: Path) -> dict[tuple[str, str], list[str]]:
    """Default keyboard bindings from the p4k. Raises P4KError.

    Parsed once per (size, mtime) of the p4k, like p4k.get_archive; concurrent
    callers serialize on the lock and the later ones hit the cache.
    """
    p4k_path = Path(p4k_path)
    try:
        st = p4k_path.stat()
    except OSError as exc:
        raise p4k.P4KError("Data.p4k not found") from exc
    sig = (st.st_size, st.st_mtime_ns)
    with _defaults_lock:
        cached = _defaults_cache.get(p4k_path)
        if cached and cached[0] == sig:
            return cached[1]
        data = p4k.read_named(p4k_path, p4k.DEFAULT_PROFILE)
        parsed = _collect(p4k.parse_xml(data))
        _defaults_cache[p4k_path] = (sig, parsed)
        return parsed


def load_user(path: Path) -> dict[tuple[str, str], list[str]]:
    try:
        if path.stat().st_size > MAX_ACTIONMAPS_BYTES:
            raise p4k.P4KError(
                f"actionmaps.xml is larger than {MAX_ACTIONMAPS_BYTES // (1024 * 1024)} MB; refusing to read it"
            )
        data = path.read_bytes()
    except OSError as exc:
        raise p4k.P4KError(f"could not read actionmaps.xml: {exc.strerror or exc}") from exc
    return _collect(p4k.parse_xml(data))


# ── Export ──────────────────────────────────────────────────────────────────────
class KeybindError(Exception):
    """Human-readable reason an export could not be produced."""


def export(
    live_dir: Path | None,
    is_valid: Callable[[str], bool],
    channel: str | None = None,
) -> dict:
    """Build the ``keybinds`` object for the ack. Raises KeybindError."""
    if live_dir is None or not Path(live_dir).is_dir():
        raise KeybindError("Star Citizen install not found")
    live_dir = Path(live_dir)

    defaults: dict[tuple[str, str], list[str]] | None = None
    p4k_path = ci_child(live_dir, "Data.p4k")
    if p4k_path is not None:
        try:
            defaults = load_defaults(p4k_path)
        except p4k.P4KError as exc:
            logging.warning(f"[keybinds] default bindings unavailable ({exc})")
            defaults = None
    user_file = find_user_actionmaps(live_dir)
    user: dict[tuple[str, str], list[str]] = {}
    profile = "default"
    if user_file is not None:
        try:
            user = load_user(user_file[0])
            profile = user_file[1]
        except p4k.P4KError as exc:
            if defaults is None:
                raise KeybindError(f"actionmaps.xml unreadable: {exc}") from exc
            user_file = None

    if defaults is None and user_file is None:
        raise KeybindError(
            "actionmaps.xml not found and Data.p4k defaults unavailable -- "
            "launch Star Citizen once so it writes your keybinds"
        )

    # Only keyboard rebinds are merged; non-keyboard rebinds are ignored because SC keeps per-device bindings independently.
    # (map, action) -> (inputs, source). User kb rebinds replace the default
    # keyboard binding for that action; a blank input clears it.
    merged: dict[tuple[str, str], tuple[list[str], str]] = {}
    for key, inputs in (defaults or {}).items():
        merged[key] = (inputs, "default")
    for key, inputs in user.items():
        live = [i for i in inputs if not _is_blank(i)]
        if live:
            merged[key] = (live, "user")
        else:
            merged.pop(key, None)        # explicitly cleared

    bindings: list[dict] = []
    skipped = 0
    seen: set[tuple[str, str, str]] = set()
    for (amap, action), (inputs, source) in merged.items():
        for raw in inputs:
            if _is_blank(raw):
                continue
            key = translate(raw, is_valid)
            if key is None:
                skipped += 1
                continue
            ident = (amap, action, key)
            if ident in seen:
                continue
            seen.add(ident)
            bindings.append({"actionmap": amap, "action": action, "key": key, "source": source})

    return {
        "version": EXPORT_VERSION,
        "channel": channel or live_dir.name.upper(),
        "profile": profile,
        "defaultsLoaded": defaults is not None,
        "skipped": skipped,
        "bindings": bindings,
    }
