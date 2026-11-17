"""Name resolution and keybind export, against synthetic game folders.

Every key, value, action name and binding below is invented. None of it is
copied from a real install.
"""
import asyncio
import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import pytest

# The genuine package first, so test_bridge_server's stand-in is not installed
# in its place when this file is run on its own.
import websockets  # noqa: F401

from core import keybinds, p4k
from core.localization import Localization, parse_ini
from tests import p4k_builder as pb
from tests.test_bridge_server import FakeWebSocket  # also installs the bootstrap stubs

INI = (
    "﻿Stanton4_TestCity=Test City\r\n"
    "ui_test_contract=Test Contract\r\n"
    "Foo_name=Foo Outpost\r\n"
    "broken_one=\r\n"
    "todo_one=LOC_PLACEHOLDER\r\n"
    "plural_thing,P=Plural Thing\r\n"
    "no equals here\r\n"
)


def table(text=INI.lstrip("﻿")):
    loc = Localization()
    loc.load_text(text)
    return loc


# ── global.ini parse and lookups ──────────────────────────────────────────────
def test_parse_ini_is_case_insensitive_and_skips_junk():
    t = parse_ini(INI.lstrip("﻿"))
    assert t["stanton4_testcity"] == "Test City"
    assert "no equals here" not in t


def test_resolve_key_strips_at_and_ignores_case():
    loc = table()
    assert loc.resolve_key("@UI_Test_Contract") == "Test Contract"
    assert loc.resolve_key("ui_test_contract") == "Test Contract"


def test_unresolvable_values_are_none_never_empty():
    loc = table()
    assert loc.resolve_key("nope") is None
    assert loc.resolve_key("broken_one") is None       # empty value
    assert loc.resolve_key("todo_one") is None         # placeholder value
    assert loc.resolve_key("") is None and loc.resolve_key("@") is None


def test_comma_p_suffix_key_also_resolves_without_it():
    assert table().resolve_key("plural_thing") == "Plural Thing"


def test_nothing_resolves_before_the_table_is_loaded():
    loc = Localization()
    assert loc.resolve_key("x") is None and loc.resolve_location("A_B") is None
    assert loc.resolve_text("@x stays") == "@x stays"


# ── locations ─────────────────────────────────────────────────────────────────
def test_location_resolves_by_exact_code():
    assert table().resolve_location("Stanton4_TestCity") == "Test City"


def test_location_falls_back_to_documented_variants():
    assert table().resolve_location("Sys9_Foo") == "Foo Outpost"      # {tail}_name


def test_unknown_location_is_none():
    assert table().resolve_location("Nowhere_Land") is None


# ── notification text ─────────────────────────────────────────────────────────
def test_resolve_text_replaces_known_at_tokens_only():
    loc = table()
    text = "Accepted @ui_test_contract and @missing_key, mail a@ui_test_contract ~mission(ItemsToDestroy) ~action(x)"
    out = loc.resolve_text(text)
    assert out == ("Accepted Test Contract and @missing_key, mail a@ui_test_contract "
                   "~mission(ItemsToDestroy) ~action(x)")


# ── background loader ─────────────────────────────────────────────────────────
def _wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _live_dir(tmp_path, ini=INI, profile_xml=None):
    live = tmp_path / "LIVE"
    live.mkdir()
    (live / "Game.log").write_text("x\n", encoding="utf-8")
    entries = [("Data\\Localization\\english\\global.ini", ini.encode("utf-8"), 0)]
    if profile_xml is not None:
        entries.append(("Data\\Libs\\Config\\defaultProfile.xml", profile_xml, 0))
    (live / "Data.p4k").write_bytes(pb.build_p4k(entries))
    return live


def test_loader_reads_global_ini_from_the_p4k_off_thread(tmp_path):
    live = _live_dir(tmp_path)
    loc = Localization()
    loc.start(live / "Data.p4k")
    assert _wait(lambda: loc.ready)
    assert loc.resolve_location("Stanton4_TestCity") == "Test City"   # BOM stripped


def test_loader_failure_stays_unresolved_and_logs_once(tmp_path, caplog):
    loc = Localization()
    with caplog.at_level(logging.WARNING):
        loc.start(tmp_path / "Data.p4k")
        _wait(lambda: any("Name resolution unavailable" in r.message for r in caplog.records))
        loc.start(tmp_path / "Data.p4k")   # same missing file again
        time.sleep(0.2)
    assert not loc.ready
    assert sum("Name resolution unavailable" in r.message for r in caplog.records) == 1
    assert loc.resolve_location("Stanton4_TestCity") is None


def _encrypted_live_dir(tmp_path, profile_encrypted=True):
    live = tmp_path / "LIVE"
    live.mkdir()
    (live / "Game.log").write_text("x\n", encoding="utf-8")
    (live / "Data.p4k").write_bytes(pb.build_p4k([
        ("Data\\Localization\\english\\global.ini", INI.encode("utf-8"), 0, True),
        ("Data\\Libs\\Config\\defaultProfile.xml", b"<x/>", 0, profile_encrypted),
    ]))
    return live


def test_encrypted_global_ini_stays_unresolved(tmp_path, caplog):
    live = _encrypted_live_dir(tmp_path)
    loc = Localization()
    with caplog.at_level(logging.WARNING):
        loc.start(live / "Data.p4k")
        assert _wait(lambda: any("Name resolution unavailable" in r.message for r in caplog.records))
    assert not loc.ready
    assert loc.resolve_location("Stanton4_TestCity") is None


def test_encrypted_default_profile_exports_user_deltas_only(tmp_path):
    live = _encrypted_live_dir(tmp_path)
    d = live / "user" / "client" / "0" / "Profiles" / "default"
    d.mkdir(parents=True)
    (d / "actionmaps.xml").write_bytes(user_xml(("qdrive", ["kb1_q"])))
    result = keybinds.export(live, ok_all)
    assert result["defaultsLoaded"] is False
    assert by_action(result) == {"qdrive": [("Q", "user")]}


# ── telemetry enrichment ──────────────────────────────────────────────────────
@pytest.fixture
def bridge(monkeypatch):
    import bridge_server
    loc = table()
    monkeypatch.setattr(bridge_server, "name_resolver", loc)
    return bridge_server


def test_context_gets_locationname_only_when_resolved(bridge):
    ev = {"location": "Stanton4_TestCity"}
    assert bridge.enrich_event("context", ev) == {"location": "Stanton4_TestCity",
                                                  "locationName": "Test City"}
    unresolved = {"location": "Nowhere_Land"}
    out = bridge.enrich_event("context", unresolved)
    assert out == {"location": "Nowhere_Land"} and "locationName" not in out
    assert bridge.enrich_event("context", {"gameRunning": True}) == {"gameRunning": True}


def test_notification_text_resolution_keeps_other_fields(bridge):
    ev = {"text": "Got @ui_test_contract:", "missionId": "0"}
    assert bridge.enrich_event("notification", ev) == {"text": "Got Test Contract:", "missionId": "0"}
    same = {"text": "Plain ~mission(X)", "missionId": "0"}
    assert bridge.enrich_event("notification", same) is same


def test_context_frame_on_the_wire_carries_raw_and_resolved(tmp_path):
    import bridge_server
    live = _live_dir(tmp_path)
    log = live / "Game.log"
    log.write_text('<2026-08-08T02:42:26.205Z> BackupNameAttachment=" Build(12344265) x"\n',
                   encoding="utf-8")
    bridge_server._log_path_override = log
    ws = FakeWebSocket()

    async def scenario():
        handle = bridge_server.make_handler(None)
        ws.push({"type": "subscribe", "feeds": ["context"], "id": 1})
        task = asyncio.ensure_future(handle(ws))
        await asyncio.sleep(0.1)
        for _ in range(100):
            if bridge_server.name_resolver.ready:
                break
            await asyncio.sleep(0.05)
        with log.open("a", encoding="utf-8") as fh:
            fh.write("<2026-08-08T02:43:00.000Z> [Notice] <RequestLocationInventory> "
                     "Player[X] requested inventory for Location[Stanton4_TestCity] "
                     "[Team_CoreGameplayFeatures][Inventory]\n")
        await asyncio.sleep(0.9)
        ws.end()
        await task

    try:
        asyncio.run(asyncio.wait_for(scenario(), timeout=10))
    finally:
        bridge_server._log_path_override = None
        bridge_server.name_resolver.start(None)
    events = [f["event"] for f in ws.telemetry_frames("context") if "location" in f["event"]]
    assert events == [{"location": "Stanton4_TestCity", "locationName": "Test City"}]


# ── keybind translation ───────────────────────────────────────────────────────
def ok_all(_):
    return True


@pytest.mark.parametrize("sc, expected", [
    ("n", "N"), ("lalt+n", "LALT+N"), ("np_1", "NUM1"), ("f1", "F1"),
    ("lctrl+lshift+k", "LCTRL+LSHIFT+K"), ("np_add", "NUMPLUS"), ("ralt+np_0", "RALT+NUM0"),
    ("backslash", "\\"), ("pgup", "PAGEUP"), ("escape", "ESC"),
])
def test_translate_known_inputs(sc, expected):
    assert keybinds.translate(sc, ok_all) == expected


@pytest.mark.parametrize("sc", ["", " ", "weirdkey", "lalt+", "+", "lshift+lalt", "lfoo+n"])
def test_translate_rejects_inexpressible(sc):
    assert keybinds.translate(sc, ok_all) is None


def test_translate_defers_to_the_bridge_validator():
    assert keybinds.translate("n", lambda s: False) is None


def test_keyboard_token_classifies_devices():
    assert keybinds.keyboard_token("kb1_lalt+n") == "lalt+n"
    assert keybinds.keyboard_token("kb1_ ") == " "
    assert keybinds.keyboard_token("js1_button3") is None
    assert keybinds.keyboard_token("mo1_button1") is None
    assert keybinds.keyboard_token("gp1_a") is None
    assert keybinds.keyboard_token("n") == "n"


# ── keybind export ────────────────────────────────────────────────────────────
def profile_xml(*actions, binary=True):
    root = ET.Element("profile")
    amap = ET.SubElement(root, "actionmap", name="spaceship_test")
    for name, attrs in actions:
        ET.SubElement(amap, "action", name=name, **attrs)
    return pb.build_cryxmlb(root) if binary else ET.tostring(root)


DEFAULTS = profile_xml(
    ("landing", {"keyboard": "n", "joystick": "js1_button1"}),
    ("qdrive", {"keyboard": "b"}),
    ("lights", {"keyboard": "l"}),
    ("stick_only", {"joystick": "js1_button2"}),
    ("camera", {"keyboard": "ralt+np_1"}),
    ("odd", {"keyboard": "f13"}),
    ("padded", {"keyboard": "kb1_u", "gamepad": "gp1_a"}),
)


def user_xml(*actions, profile="default"):
    root = ET.Element("ActionMaps")
    profiles = ET.SubElement(root, "ActionProfiles", profileName=profile)
    amap = ET.SubElement(profiles, "actionmap", name="spaceship_test")
    for name, inputs in actions:
        a = ET.SubElement(amap, "action", name=name)
        for i in inputs:
            ET.SubElement(a, "rebind", input=i)
    return ET.tostring(root)


def install(tmp_path, defaults=DEFAULTS, user=None, profile="default", user_dirs=("user", "client", "0", "Profiles")):
    live = _live_dir(tmp_path, profile_xml=defaults)
    if defaults is None:
        (live / "Data.p4k").unlink()
    if user is not None:
        d = live.joinpath(*user_dirs, profile)
        d.mkdir(parents=True)
        (d / "actionmaps.xml").write_bytes(user)
    return live


def by_action(result):
    out = {}
    for b in result["bindings"]:
        out.setdefault(b["action"], []).append((b["key"], b["source"]))
    return out


def test_defaults_only_drops_non_keyboard_and_counts_skips(tmp_path):
    result = keybinds.export(install(tmp_path), ok_all)
    got = by_action(result)
    assert got["landing"] == [("N", "default")]
    assert got["camera"] == [("RALT+NUM1", "default")]
    assert got["padded"] == [("U", "default")]
    assert "stick_only" not in got
    assert result["defaultsLoaded"] is True and result["profile"] == "default"
    assert result["channel"] == "LIVE" and result["version"] == 1


def test_untranslatable_binding_is_skipped_not_exported(tmp_path):
    def valid(key):
        return key != "F13"
    result = keybinds.export(install(tmp_path), valid)
    assert "odd" not in by_action(result)
    assert result["skipped"] == 1


def test_user_rebind_overrides_default(tmp_path):
    user = user_xml(("qdrive", ["kb1_lalt+q"]))
    got = by_action(keybinds.export(install(tmp_path, user=user), ok_all))
    assert got["qdrive"] == [("LALT+Q", "user")]
    assert got["landing"] == [("N", "default")]


def test_user_clear_removes_the_action(tmp_path):
    user = user_xml(("lights", ["kb1_ "]))
    got = by_action(keybinds.export(install(tmp_path, user=user), ok_all))
    assert "lights" not in got and "landing" in got


def test_joystick_rebind_leaves_the_default_keyboard_binding(tmp_path):
    user = user_xml(("landing", ["js1_button9"]))
    got = by_action(keybinds.export(install(tmp_path, user=user), ok_all))
    assert got["landing"] == [("N", "default")]


def test_user_only_action_and_cleared_joystick_do_not_leak(tmp_path):
    user = user_xml(("brand_new", ["kb1_j", "js1_button4"]))
    got = by_action(keybinds.export(install(tmp_path, user=user), ok_all))
    assert got["brand_new"] == [("J", "user")]


def test_plain_xml_defaults_and_rebind_children_are_understood(tmp_path):
    root = ET.Element("profile")
    amap = ET.SubElement(root, "actionmap", name="m")
    a = ET.SubElement(amap, "action", name="x")
    ET.SubElement(a, "rebind", input="kb1_h")
    live = install(tmp_path, defaults=ET.tostring(root))
    assert by_action(keybinds.export(live, ok_all))["x"] == [("H", "default")]


def test_profile_prefers_default_then_most_recent(tmp_path):
    live = install(tmp_path, user=user_xml(("qdrive", ["kb1_q"]), profile="Custom"), profile="Custom")
    result = keybinds.export(live, ok_all)
    assert result["profile"] == "Custom"           # no "default": fall back to what exists
    other = live / "user" / "client" / "0" / "Profiles" / "default"
    other.mkdir()
    (other / "actionmaps.xml").write_bytes(user_xml(("qdrive", ["kb1_w"])))
    result = keybinds.export(live, ok_all)
    assert result["profile"] == "default" and by_action(result)["qdrive"] == [("W", "user")]


def test_unknown_profile_picks_most_recently_modified(tmp_path):
    live = install(tmp_path, user=user_xml(("qdrive", ["kb1_q"]), profile="old"), profile="old")
    newer = live / "user" / "client" / "0" / "Profiles" / "new"
    newer.mkdir()
    f = newer / "actionmaps.xml"
    f.write_bytes(user_xml(("qdrive", ["kb1_e"])))
    old_f = live / "user" / "client" / "0" / "Profiles" / "old" / "actionmaps.xml"
    import os
    os.utime(old_f, (1_000_000, 1_000_000))
    assert keybinds.export(live, ok_all)["profile"] == "new"


def test_directory_names_match_case_insensitively(tmp_path):
    live = install(tmp_path, user=user_xml(("qdrive", ["kb1_q"])),
                   user_dirs=("User", "Client", "0", "profiles"))
    assert by_action(keybinds.export(live, ok_all))["qdrive"] == [("Q", "user")]


def test_missing_p4k_returns_user_deltas_only(tmp_path):
    live = install(tmp_path, defaults=None, user=user_xml(("qdrive", ["kb1_q"])))
    result = keybinds.export(live, ok_all)
    assert result["defaultsLoaded"] is False
    assert by_action(result) == {"qdrive": [("Q", "user")]}


def test_p4k_without_the_default_profile_is_not_fatal(tmp_path):
    live = _live_dir(tmp_path)         # global.ini only
    d = live / "user" / "client" / "0" / "Profiles" / "default"
    d.mkdir(parents=True)
    (d / "actionmaps.xml").write_bytes(user_xml(("qdrive", ["kb1_q"])))
    assert keybinds.export(live, ok_all)["defaultsLoaded"] is False


def test_missing_actionmaps_but_defaults_available_still_exports(tmp_path):
    result = keybinds.export(install(tmp_path), ok_all)
    assert result["defaultsLoaded"] is True and result["bindings"]


def test_nothing_available_is_an_error(tmp_path):
    live = install(tmp_path, defaults=None)
    with pytest.raises(keybinds.KeybindError, match="actionmaps.xml not found"):
        keybinds.export(live, ok_all)


def test_missing_install_is_an_error(tmp_path):
    with pytest.raises(keybinds.KeybindError, match="install not found"):
        keybinds.export(None, ok_all)
    with pytest.raises(keybinds.KeybindError):
        keybinds.export(tmp_path / "nope", ok_all)


def test_corrupt_user_file_with_no_defaults_is_an_error(tmp_path):
    live = install(tmp_path, defaults=None, user=b"<ActionMaps><oops")
    with pytest.raises(keybinds.KeybindError, match="unreadable"):
        keybinds.export(live, ok_all)


# ── the wire ──────────────────────────────────────────────────────────────────
def run_keybinds_request(tmp_path, live, message):
    import bridge_server
    log = live / "Game.log"
    bridge_server._log_path_override = log
    ws = FakeWebSocket()

    async def scenario():
        handle = bridge_server.make_handler(None)
        ws.push(message)
        task = asyncio.ensure_future(handle(ws))
        await asyncio.sleep(0.5)
        ws.end()
        await task

    try:
        asyncio.run(asyncio.wait_for(scenario(), timeout=10))
    finally:
        bridge_server._log_path_override = None
    return ws.sent


def test_keybinds_request_is_acked_with_the_documented_shape(tmp_path):
    live = install(tmp_path, user=user_xml(("qdrive", ["kb1_lalt+q"])))
    sent = run_keybinds_request(tmp_path, live, {"type": "keybinds", "id": 7})
    assert len(sent) == 1
    ack = sent[0]
    assert ack["type"] == "ack" and ack["ok"] is True and ack["id"] == 7
    kb = ack["keybinds"]
    assert set(kb) == {"version", "channel", "profile", "defaultsLoaded", "skipped", "bindings"}
    assert {"actionmap": "spaceship_test", "action": "qdrive", "key": "LALT+Q",
            "source": "user"} in kb["bindings"]
    assert {"actionmap": "spaceship_test", "action": "landing", "key": "N",
            "source": "default"} in kb["bindings"]
    assert all(set(b) == {"actionmap", "action", "key", "source"} for b in kb["bindings"])


def test_keybinds_without_an_id_gets_no_reply(tmp_path):
    live = install(tmp_path)
    assert run_keybinds_request(tmp_path, live, {"type": "keybinds"}) == []


def test_keybinds_error_ack_when_nothing_is_readable(tmp_path):
    live = install(tmp_path, defaults=None)
    sent = run_keybinds_request(tmp_path, live, {"type": "keybinds", "id": 3})
    assert sent[0]["ok"] is False and sent[0]["id"] == 3 and "actionmaps.xml" in sent[0]["error"]
    assert "keybinds" not in sent[0]


def test_oversized_export_is_refused_not_sent(tmp_path):
    live = install(tmp_path)
    with mock.patch.object(keybinds, "MAX_ACK_BYTES", 50):
        sent = run_keybinds_request(tmp_path, live, {"type": "keybinds", "id": 4})
    assert sent[0]["ok"] is False and "too large" in sent[0]["error"]


# ── keybind hardening ─────────────────────────────────────────────────────────
def test_oversized_actionmaps_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(keybinds, "MAX_ACTIONMAPS_BYTES", 50)
    live = install(tmp_path, user=user_xml(("landing", ["kb1_h"])))
    # Defaults still load, so the export degrades to defaults instead of reading the file.
    result = keybinds.export(live, ok_all)
    assert all(b["source"] == "default" for b in result["bindings"])
    with pytest.raises(p4k.P4KError, match="larger than"):
        keybinds.load_user(next(live.rglob("actionmaps.xml")))


def test_defaults_are_cached_by_p4k_signature(tmp_path, monkeypatch):
    live = install(tmp_path)
    calls = []
    real = p4k.read_named
    monkeypatch.setattr(p4k, "read_named", lambda *a: calls.append(a) or real(*a))
    path = keybinds.ci_child(live, "Data.p4k")
    keybinds._defaults_cache.pop(path, None)
    keybinds.load_defaults(path)
    keybinds.load_defaults(path)
    assert len(calls) == 1
    path.write_bytes(path.read_bytes() + b"\0")   # size changes -> invalidated
    try:
        keybinds.load_defaults(path)
    except p4k.P4KError:
        pass   # appended byte may break the synthetic archive; the re-read is what matters
    assert len(calls) == 2
