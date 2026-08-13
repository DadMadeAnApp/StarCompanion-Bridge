"""Tests for bridge_server.py's key-name resolution.

Run: python -m unittest discover -v   (from the repo root)

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
            # Real pynput Buttons are enum members, so the mouse branch's
            # log lines read `button.name`.
            self.name = name

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
        def __init__(self):
            # Recorded so a test can prove a held click actually pressed and
            # released the same button, in that order.
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


# ── Telemetry: subscribe/unsubscribe interception, the pump, and feed_task ──
import asyncio
import contextlib
import json
import time
import tempfile
from pathlib import Path
from unittest import mock


class FakeWebSocket:
    """Just enough of a websockets.ServerConnection for make_handler(None).

    Feeds `handle` from an asyncio.Queue instead of a real socket, so a test
    can push messages, let the handler react, and then end the connection
    (StopAsyncIteration) to drive the same `finally` cleanup a real close
    would -- feed_task cancellation included.
    """

    def __init__(self):
        self._incoming: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.remote_address = ("127.0.0.1", 55555)
        self.closed = False

    def push(self, message: dict) -> None:
        self._incoming.put_nowait(json.dumps(message))

    def end(self) -> None:
        self._incoming.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self, code=None, reason=None) -> None:
        self.closed = True

    def telemetry_frames(self, feed: str | None = None) -> list[dict]:
        frames = [m for m in self.sent if m.get("type") == "telemetry"]
        if feed is not None:
            frames = [f for f in frames if f.get("feed") == feed]
        return frames


class TelemetryHandlerTests(unittest.TestCase):
    """Exercises the real handler/pump, not a reimplementation of either."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.log_path = Path(self.dir.name) / "Game.log"
        self.log_path.write_text(
            '<2026-08-08T02:42:26.205Z> BackupNameAttachment=" Build(12344265) 07 Aug 26"\n',
            encoding="utf-8",
        )
        bridge_server._log_path_override = self.log_path
        self.addCleanup(setattr, bridge_server, "_log_path_override", None)

    def _run(self, coro):
        return asyncio.run(asyncio.wait_for(coro, timeout=5))

    def test_subscribe_acks_and_delivers_a_real_frame_through_the_pump(self):
        ws = FakeWebSocket()

        async def scenario():
            handle = bridge_server.make_handler(None)
            ws.push({"type": "subscribe", "feeds": ["context"], "id": 1})
            task = asyncio.ensure_future(handle(ws))
            await asyncio.sleep(0.1)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    "<2026-08-08T02:43:00.000Z> [Notice] <RequestLocationInventory> "
                    "Player[X] requested inventory for Location[Stanton4_NewBabbage] "
                    "[Team_CoreGameplayFeatures][Inventory]\n"
                )
            # LogTailer's default poll_interval is 0.25s and a record with no
            # follow-up needs two quiet polls before it flushes.
            await asyncio.sleep(0.8)
            ws.end()
            await task

        self._run(scenario())

        acks = [m for m in ws.sent if m.get("type") == "ack"]
        self.assertTrue(acks[0]["ok"])
        self.assertEqual(acks[0]["feeds"], ["context"])

        context_frames = ws.telemetry_frames("context")
        locations = [f["event"].get("location") for f in context_frames]
        self.assertIn("Stanton4_NewBabbage", locations)

    def test_unsubscribe_stops_delivering_new_frames(self):
        ws = FakeWebSocket()

        async def scenario():
            handle = bridge_server.make_handler(None)
            ws.push({"type": "subscribe", "feeds": ["context"], "id": 1})
            task = asyncio.ensure_future(handle(ws))
            await asyncio.sleep(0.1)
            ws.push({"type": "unsubscribe", "id": 2})
            await asyncio.sleep(0.05)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    "<2026-08-08T02:43:00.000Z> [Notice] <RequestLocationInventory> "
                    "Player[X] requested inventory for Location[ShouldNotArrive] "
                    "[Team_CoreGameplayFeatures][Inventory]\n"
                )
            await asyncio.sleep(0.8)
            ws.end()
            await task

        self._run(scenario())

        locations = [f["event"].get("location") for f in ws.telemetry_frames("context")]
        self.assertNotIn("ShouldNotArrive", locations)

    def test_subscribe_and_unsubscribe_never_reach_command_dispatch(self):
        """Guards the defensive dead branch in _dispatch: the message loop
        must intercept these two types itself."""
        ws = FakeWebSocket()

        async def scenario():
            with mock.patch.object(
                bridge_server, "_dispatch",
                side_effect=AssertionError("subscribe/unsubscribe reached _dispatch"),
            ):
                handle = bridge_server.make_handler(None)
                ws.push({"type": "subscribe", "feeds": ["context"], "id": 1})
                task = asyncio.ensure_future(handle(ws))
                await asyncio.sleep(0.1)
                ws.push({"type": "unsubscribe", "id": 2})
                await asyncio.sleep(0.1)
                ws.end()
                await task

        self._run(scenario())  # would raise inside the task if the guard failed
        acks = [m for m in ws.sent if m.get("type") == "ack"]
        self.assertEqual(len(acks), 2)
        self.assertTrue(acks[0]["ok"])
        self.assertTrue(acks[1]["ok"])

    def test_log_locked_is_reported_as_ok_false_not_a_silent_hang(self):
        """CRITICAL 1a: an existing-but-unopenable log must not ack ok:true.

        Denies the open by patching Path.open rather than chmod(0), because
        on Windows chmod only toggles the read-only bit and cannot revoke
        read permission -- a chmod-based version of this test passes on POSIX
        and silently fails here. Patching also models the real case better:
        the game or an AV product holding a share-lock raises PermissionError
        from open(), it does not change the file's mode.
        """
        ws = FakeWebSocket()
        real_open = Path.open

        def deny(self_path, *a, **kw):
            if self_path == self.log_path:
                raise PermissionError(13, "Permission denied")
            return real_open(self_path, *a, **kw)

        async def scenario():
            handle = bridge_server.make_handler(None)
            ws.push({"type": "subscribe", "feeds": ["context"], "id": 1})
            task = asyncio.ensure_future(handle(ws))
            await asyncio.sleep(0.1)
            ws.end()
            await task

        with mock.patch.object(Path, "open", deny):
            self._run(scenario())

        acks = [m for m in ws.sent if m.get("type") == "ack"]
        self.assertFalse(acks[0]["ok"])
        self.assertEqual(acks[0]["error"], "log locked")
        # No pump should ever have started, so no telemetry frames either.
        self.assertEqual(ws.telemetry_frames(), [])

    def test_gameRunning_snapshot_sent_on_subscribe_then_flips_true_on_first_record(self):
        """CRITICAL 2: §6.1's context event fires right on subscribe (a
        freshly-constructed LogTailer has never seen a line yet, so it reads
        as stale until the pump's first record proves otherwise -- that's
        the honest signal, not a bug: the bridge genuinely doesn't know the
        game is running until the log moves), and then flips to true the
        moment the pump sees the log actually growing."""
        ws = FakeWebSocket()

        async def scenario():
            handle = bridge_server.make_handler(None)
            ws.push({"type": "subscribe", "feeds": ["context"], "id": 1})
            task = asyncio.ensure_future(handle(ws))
            await asyncio.sleep(0.1)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    "<2026-08-08T02:43:00.000Z> [Notice] <RequestLocationInventory> "
                    "Player[X] requested inventory for Location[Somewhere] "
                    "[Team_CoreGameplayFeatures][Inventory]\n"
                )
            await asyncio.sleep(0.8)
            ws.end()
            await task

        self._run(scenario())

        game_running_frames = [
            f for f in ws.telemetry_frames("context") if "gameRunning" in f["event"]
        ]
        self.assertEqual([f["event"]["gameRunning"] for f in game_running_frames], [False, True])

    def test_gameRunning_context_is_not_sent_to_a_feed_that_did_not_subscribe(self):
        ws = FakeWebSocket()

        async def scenario():
            handle = bridge_server.make_handler(None)
            ws.push({"type": "subscribe", "feeds": ["mission"], "id": 1})
            task = asyncio.ensure_future(handle(ws))
            await asyncio.sleep(0.1)
            ws.end()
            await task

        self._run(scenario())
        self.assertEqual(ws.telemetry_frames("context"), [])

    def test_pump_error_clears_feed_task_so_a_resubscribe_restarts_it(self):
        """CRITICAL 1b: whatever kills the pump, feed_task must not stay
        non-None -- otherwise the double-start guard blocks every future
        resubscribe on this connection forever."""
        real_watch = bridge_server.LogTailer.watch
        state = {"calls": 0}

        async def flaky_watch(self):
            state["calls"] += 1
            if state["calls"] == 1:
                raise OSError("simulated: log vanished after subscribe")
            async for record in real_watch(self):
                yield record

        ws = FakeWebSocket()

        async def scenario():
            with mock.patch.object(bridge_server.LogTailer, "watch", flaky_watch):
                handle = bridge_server.make_handler(None)
                ws.push({"type": "subscribe", "feeds": ["context"], "id": 1})
                task = asyncio.ensure_future(handle(ws))
                await asyncio.sleep(0.15)  # let the first pump die

                # Resubscribing must create a fresh pump, not be swallowed by
                # a feed_task the dead pump never cleared.
                ws.push({"type": "subscribe", "feeds": ["context"], "id": 2})
                await asyncio.sleep(0.1)
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(
                        "<2026-08-08T02:43:00.000Z> [Notice] <RequestLocationInventory> "
                        "Player[X] requested inventory for Location[Recovered] "
                        "[Team_CoreGameplayFeatures][Inventory]\n"
                    )
                await asyncio.sleep(0.8)
                ws.end()
                await task

        self._run(scenario())

        acks = [m for m in ws.sent if m.get("type") == "ack"]
        self.assertTrue(acks[0]["ok"])   # first subscribe still acks ok
        self.assertTrue(acks[1]["ok"])   # second subscribe (the resubscribe)
        locations = [f["event"].get("location") for f in ws.telemetry_frames("context")]
        self.assertIn("Recovered", locations)

    def test_feed_task_is_cancelled_when_the_connection_closes(self):
        """The pump must not keep consuming the tailer after `handle` returns
        -- covers the feed_task.cancel() path in the connection's finally."""
        state = {"iterations": 0}
        real_watch = bridge_server.LogTailer.watch

        async def counting_watch(self):
            async for record in real_watch(self):
                state["iterations"] += 1
                yield record

        ws = FakeWebSocket()

        async def scenario():
            with mock.patch.object(bridge_server.LogTailer, "watch", counting_watch):
                handle = bridge_server.make_handler(None)
                ws.push({"type": "subscribe", "feeds": ["context"], "id": 1})
                task = asyncio.ensure_future(handle(ws))
                await asyncio.sleep(0.1)
                ws.end()
                await task
                # The handler has returned (and so cancelled feed_task); any
                # further writes to the log must not be observed.
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(
                        "<2026-08-08T02:43:00.000Z> [Notice] <RequestLocationInventory> "
                        "Player[X] requested inventory for Location[AfterClose] "
                        "[Team_CoreGameplayFeatures][Inventory]\n"
                    )
                await asyncio.sleep(0.2)

        self._run(scenario())
        locations = [f["event"].get("location") for f in ws.telemetry_frames("context")]
        self.assertNotIn("AfterClose", locations)

    def test_pump_burns_seq_on_a_rate_limited_frame_end_to_end(self):
        """MINOR 6 replacement: drives the real pump/limiter/session path
        (not a reimplementation of the ordering in the test body) by
        appending mission records faster than TELEMETRY_MAX_PER_SECOND
        allows, and checking the *delivered* frames for a seq gap."""
        ws = FakeWebSocket()

        upsert = (
            "<2026-08-08T02:49:20.{ms:03d}Z> [Notice] <ObjectiveUpserted> Received "
            "ObjectiveUpserted push message for: mission_id "
            "94c24d22-cf1f-4e79-bdce-b35f3ee499c0 - objective_id "
            "{oid} - state MISSION_OBJECTIVE_STATE_INPROGRESS - created 1 - "
            "flags=ShowInLog| [Team_GameServices][Missions]\n"
            "<2026-08-08T02:49:20.{ms:03d}Z> [Notice] <CMissionLogEntry::UpdateActiveObjective> "
            "Objective updated id={oid}, flags=ShowInLog|, hidden=0, hiddenInUI=0, "
            "markerHidden=0, uiDisplay[Priority=1][Text=Go] [Team_MissionFeatures][Missions]\n"
        )

        async def scenario():
            with mock.patch.object(bridge_server, "TELEMETRY_MAX_PER_SECOND", 5):
                handle = bridge_server.make_handler(None)
                ws.push({"type": "subscribe", "feeds": ["mission"], "id": 1})
                task = asyncio.ensure_future(handle(ws))
                await asyncio.sleep(0.1)
                # Spread the writes into bursts separated by more than one
                # poll tick so the token bucket only partially refills
                # between them -- a single instantaneous burst just drops
                # one contiguous tail with no gap *between delivered frames*,
                # which wouldn't exercise what §6.3 cares about.
                for chunk in range(6):
                    with self.log_path.open("a", encoding="utf-8") as fh:
                        for i in range(5):
                            n = chunk * 5 + i
                            oid = f"{n:08x}-0000-0000-0000-000000000000"
                            fh.write(upsert.format(ms=n % 1000, oid=oid))
                    await asyncio.sleep(0.35)
                ws.end()
                await task

        self._run(scenario())

        seqs = [f["seq"] for f in ws.telemetry_frames("mission")]
        self.assertGreater(len(seqs), 0)
        self.assertLess(len(seqs), 30)  # the rate cap must have dropped some
        gaps = [b - a for a, b in zip(seqs, seqs[1:])]
        self.assertTrue(any(gap > 1 for gap in gaps), "expected a seq gap from a dropped frame")


class MouseHoldTests(unittest.TestCase):
    """A held left click is Star Citizen's quantum jump, so `click` with a
    `hold` must be a press/sleep/release, not a tap."""

    def setUp(self):
        self.mouse = bridge_server._mouse
        self.mouse.events.clear()

    def _dispatch(self, msg):
        return asyncio.new_event_loop().run_until_complete(bridge_server._dispatch(msg))

    def test_click_with_hold_presses_and_releases_the_same_button(self):
        ok, err = self._dispatch({"type": "mouse", "action": "click", "hold": 0.05})
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(
            self.mouse.events,
            [("press", bridge_server.Button.left), ("release", bridge_server.Button.left)],
        )

    def test_click_without_hold_is_still_a_tap(self):
        ok, _err = self._dispatch({"type": "mouse", "action": "click"})
        self.assertTrue(ok)
        self.assertEqual(self.mouse.events, [("click", bridge_server.Button.left)])

    def test_hold_actually_waits(self):
        started = time.monotonic()
        self._dispatch({"type": "mouse", "action": "click", "hold": 0.3})
        self.assertGreaterEqual(time.monotonic() - started, 0.25)

    def test_hold_is_clamped_to_the_maximum(self):
        # Shares _coerce_hold with the keyboard path, so a client asking for a
        # minute cannot pin the mouse button down for one.
        with mock.patch.object(bridge_server, "MAX_HOLD_SECONDS", 0.05):
            started = time.monotonic()
            self._dispatch({"type": "mouse", "action": "click", "hold": 60})
        self.assertLess(time.monotonic() - started, 5)

    def test_zero_hold_degrades_to_a_tap(self):
        # _coerce_hold returns None for <= 0, so the tap branch takes it and the
        # button is never left in a pressed-only state.
        self._dispatch({"type": "mouse", "action": "click", "hold": 0})
        self.assertEqual(self.mouse.events, [("click", bridge_server.Button.left)])

    def test_button_is_released_even_if_the_hold_is_cancelled(self):
        """A dropped connection mid-hold must never leave a button stuck down."""
        async def scenario():
            task = asyncio.ensure_future(
                bridge_server._dispatch({"type": "mouse", "action": "click", "hold": 5})
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        asyncio.new_event_loop().run_until_complete(scenario())
        self.assertEqual(
            self.mouse.events,
            [("press", bridge_server.Button.left), ("release", bridge_server.Button.left)],
        )

    def test_hold_honours_the_button_field(self):
        self._dispatch({"type": "mouse", "action": "click", "button": "right", "hold": 0.05})
        self.assertEqual(
            self.mouse.events,
            [("press", bridge_server.Button.right), ("release", bridge_server.Button.right)],
        )


if __name__ == "__main__":
    unittest.main()
