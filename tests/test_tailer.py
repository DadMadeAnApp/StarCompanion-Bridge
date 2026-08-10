import asyncio
import tempfile
import unittest
from pathlib import Path

from telemetry import parsers, tailer


class TailerTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "Game.log"
        self.path.write_text(
            '<2026-08-08T02:42:26.205Z> BackupNameAttachment=" Build(12344265) 07 Aug 26"\n',
            encoding="utf-8",
        )
        self.addCleanup(self.dir.cleanup)

    def _collect(self, appender, expected):
        async def run():
            t = tailer.LogTailer(self.path, poll_interval=0.01)
            seen = []

            async def consume():
                async for record in t.watch():
                    seen.append(record)
                    if len(seen) >= expected:
                        return

            task = asyncio.ensure_future(consume())
            await asyncio.sleep(0.05)
            appender()
            await asyncio.wait_for(task, timeout=3)
            return seen

        return asyncio.run(run())

    def test_reads_the_build_from_the_existing_header(self):
        t = tailer.LogTailer(self.path)
        self.assertEqual(t.game_build, "12344265")

    def test_yields_records_appended_after_start(self):
        def append():
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write("<2026-08-08T02:49:20.967Z> [Notice] <ObjectiveUpserted> hello\n")

        seen = self._collect(append, 1)
        self.assertIn("hello", seen[0])

    def test_joins_a_wrapped_record_before_yielding(self):
        def append():
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write('<2026-08-08T02:49:20.967Z> [Notice] <SHUDEvent_OnNotification> "wrapped\n')
                fh.write('tail" [1] to queue.\n')
                fh.write("<2026-08-08T02:49:21.000Z> [Notice] <Other> next\n")

        seen = self._collect(append, 1)
        self.assertIn("wrapped", seen[0])
        self.assertIn("tail", seen[0])

    def test_reopens_after_truncation(self):
        def append():
            self.path.write_text(
                '<2026-08-08T03:00:00.000Z> BackupNameAttachment=" Build(99999999) 08 Aug 26"\n'
                "<2026-08-08T03:00:01.000Z> [Notice] <ObjectiveUpserted> fresh session\n",
                encoding="utf-8",
            )

        seen = self._collect(append, 1)
        self.assertIn("fresh session", seen[0])

    def test_survives_the_quiet_flush_race(self):
        """A wrapped notification whose two physical lines straddle a poll
        tick must still arrive as one whole record, MissionId intact.

        Regression for: watch() used to flush a buffered record on the FIRST
        empty read, so a continuation landing just after that poll fired was
        never joined — parse_notification then silently failed to match
        (no MissionId on the truncated record) and the notification vanished.
        """

        async def run():
            t = tailer.LogTailer(self.path, poll_interval=0.1)
            seen = []

            async def consume():
                async for record in t.watch():
                    seen.append(record)
                    return

            task = asyncio.ensure_future(consume())
            await asyncio.sleep(0.05)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(
                    '<2026-08-08T02:44:59.361Z> [Notice] <SHUDEvent_OnNotification> '
                    'Added notification "You have joined channel \'Test Org\'.\n'
                )
            # Straddle one poll tick (comfortably less than the two-quiet-poll
            # flush threshold) before the continuation lands.
            await asyncio.sleep(0.12)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(
                    '" [2] to queue. New queue size: 1, '
                    "MissionId: [00000000-0000-0000-0000-000000000000], ObjectiveId: []\n"
                )
            await asyncio.wait_for(task, timeout=3)
            return seen

        seen = asyncio.run(run())
        self.assertEqual(len(seen), 1)
        self.assertIn("joined channel", seen[0])
        self.assertIn("to queue", seen[0])
        self.assertIn("MissionId: [00000000-0000-0000-0000-000000000000]", seen[0])

    def test_strips_carriage_returns_from_crlf_lines(self):
        """Windows' Game.log may use CRLF. A stray '\\r' before every internal
        join, and a trailing one on the record, would ride the wire into the
        HUD notification's "text" field verbatim -- a visible defect on the
        client. The assembled record must contain no '\\r' at all, and the
        real newline between physical lines must survive so
        parse_notification's re.DOTALL match still spans it."""
        crlf_path = Path(self.dir.name) / "GameCRLF.log"
        with crlf_path.open("wb") as fh:
            fh.write(
                b'<2026-08-08T02:42:26.205Z> BackupNameAttachment=" Build(12344265) 07 Aug 26"\r\n'
            )

        async def run():
            t = tailer.LogTailer(crlf_path, poll_interval=0.01)
            seen = []

            async def consume():
                async for record in t.watch():
                    seen.append(record)
                    return

            task = asyncio.ensure_future(consume())
            await asyncio.sleep(0.03)
            with crlf_path.open("ab") as fh:
                fh.write(
                    b'<2026-08-08T02:44:59.361Z> [Notice] <SHUDEvent_OnNotification> '
                    b'Added notification "You have joined channel \'Test Org\'.\r\n'
                )
                fh.write(
                    b'" [2] to queue. New queue size: 1, '
                    b"MissionId: [00000000-0000-0000-0000-000000000000], ObjectiveId: []\r\n"
                )
                fh.write(b"<2026-08-08T02:45:00.000Z> [Notice] <Other> next\r\n")
            await asyncio.wait_for(task, timeout=3)
            return seen

        seen = asyncio.run(run())
        self.assertEqual(len(seen), 1)
        self.assertNotIn("\r", seen[0])
        event = parsers.parse_notification(seen[0])
        self.assertIsNotNone(event)
        self.assertIn("joined channel", event["text"])
        self.assertEqual(event["missionId"], "00000000-0000-0000-0000-000000000000")

    def test_probe_succeeds_on_a_readable_file(self):
        t = tailer.LogTailer(self.path)
        t.probe()  # must not raise

    def test_probe_raises_on_an_unreadable_file(self):
        t = tailer.LogTailer(self.path)
        self.path.chmod(0)
        self.addCleanup(lambda: self.path.chmod(0o644))
        try:
            with self.assertRaises(OSError):
                t.probe()
        finally:
            self.path.chmod(0o644)

    def test_watch_yields_control_during_a_line_burst(self):
        """A burst of many buffered lines must not run the whole while-loop
        synchronously -- that would starve the event loop that command
        dispatch also runs on. Regression guard: patch asyncio.sleep to
        record every call the loop makes; a burst bigger than the yield
        threshold must trigger at least one, even though every line is
        already sitting in the file (no read ever comes back empty)."""
        lines = "".join(
            f"<2026-08-08T02:49:{i % 60:02d}.000Z> [Notice] <Other> line {i}\n"
            for i in range(450)
        )

        async def run():
            t = tailer.LogTailer(self.path, poll_interval=0.01)
            sleeps = []
            real_sleep = asyncio.sleep

            async def counting_sleep(delay, *a, **k):
                sleeps.append(delay)
                await real_sleep(0)

            seen = []

            async def consume():
                async for record in t.watch():
                    seen.append(record)
                    if len(seen) >= 450:
                        return

            asyncio.sleep = counting_sleep
            try:
                task = asyncio.ensure_future(consume())
                await real_sleep(0.05)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(lines)
                await asyncio.wait_for(task, timeout=5)
            finally:
                asyncio.sleep = real_sleep
            return sleeps

        sleeps = asyncio.run(run())
        # At least one yield-for-a-burst call (delay 0), distinct from the
        # quiet-poll waits (delay == poll_interval), must have happened.
        self.assertIn(0, sleeps)

    def test_a_spam_line_does_not_glue_onto_the_live_record_before_it(self):
        """Pins the same fix on the live tailer path, not just the pure
        parser: a '[SPAM N][Error]' line arriving after a whitelisted record
        must start its own record rather than being appended as a
        continuation, or its text corrupts the record before it on the wire."""

        def append():
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(
                    "<2026-08-08T03:28:58.406Z> [Notice] <ObjectiveUpserted> real record\n"
                )
                fh.write(
                    "<2026-08-08T03:29:02.041Z> [SPAM 135][Error] <Fire Simulation> "
                    "Radiation Heat\n"
                )
                fh.write("<2026-08-08T03:29:03.000Z> [Notice] <Other> next\n")

        seen = self._collect(append, 2)
        self.assertIn("real record", seen[0])
        self.assertNotIn("Radiation Heat", seen[0])
        self.assertIn("SPAM 135", seen[1])


if __name__ == "__main__":
    unittest.main()
