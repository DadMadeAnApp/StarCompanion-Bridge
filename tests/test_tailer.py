import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from telemetry import parsers, tailer
from telemetry.tailer import launcher_log_candidates


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
        """Patch the open rather than chmod(0): on Windows chmod only toggles
        the read-only bit and cannot revoke read permission, so a chmod-based
        version of this test passes on POSIX and silently fails here. Patching
        is also the more faithful simulation -- the case probe() exists for is
        the game or an AV product holding a share-lock, which surfaces as
        PermissionError from open(), not as a mode change."""
        t = tailer.LogTailer(self.path)
        real_open = Path.open

        def deny(self_path, *a, **kw):
            if self_path == self.path:
                raise PermissionError(13, "Permission denied")
            return real_open(self_path, *a, **kw)

        with mock.patch.object(Path, "open", deny):
            with self.assertRaises(OSError):
                t.probe()

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


class LauncherLogCandidateTests(unittest.TestCase):
    """The launcher log is the only authoritative source for a moved install."""

    def test_parses_a_json_escaped_path(self):
        # Exactly how the launcher writes it: doubled backslashes.
        text = r'{"path":"F:\\Robert Space Industries\\StarCitizen\\LIVE"}'
        self.assertEqual(
            launcher_log_candidates(text),
            [Path(r"F:\Robert Space Industries\StarCitizen\LIVE")],
        )

    def test_parses_a_single_backslash_path(self):
        text = r"installing to F:\Robert Space Industries\StarCitizen\LIVE now"
        self.assertEqual(
            launcher_log_candidates(text),
            [Path(r"F:\Robert Space Industries\StarCitizen\LIVE")],
        )

    def test_accepts_the_plural_spelling_too(self):
        text = r"D:\\Roberts Space Industries\\StarCitizen\\LIVE"
        self.assertEqual(
            launcher_log_candidates(text),
            [Path(r"D:\Roberts Space Industries\StarCitizen\LIVE")],
        )

    def test_live_outranks_ptu_regardless_of_position(self):
        """A PTU mention arriving later must not outrank LIVE: tailing a stale
        PTU log would follow a file nothing is writing."""
        text = (
            r"E:\\RSI\\StarCitizen\\LIVE" "\n"
            r"E:\\RSI\\StarCitizen\\PTU" "\n"
        )
        got = launcher_log_candidates(text)
        self.assertEqual(got[0], Path(r"E:\RSI\StarCitizen\LIVE"))
        self.assertIn(Path(r"E:\RSI\StarCitizen\PTU"), got)

    def test_the_newest_mention_of_a_channel_wins(self):
        """The log is append-only, so a later mention reflects a move."""
        text = (
            r"C:\\Old\\StarCitizen\\PTU" "\n"
            r"F:\\New\\StarCitizen\\PTU" "\n"
        )
        self.assertEqual(
            launcher_log_candidates(text)[0], Path(r"F:\New\StarCitizen\PTU")
        )

    def test_deduplicates_repeated_mentions(self):
        text = (r"F:\\RSI\\StarCitizen\\LIVE" "\n") * 40
        self.assertEqual(len(launcher_log_candidates(text)), 1)

    def test_a_path_with_a_trailing_filename_still_matches_the_directory(self):
        """The log mentions files inside the install constantly
        ('...\\LIVE\\loginData.json'). The match must stop at the channel dir."""
        text = r"F:\\RSI\\StarCitizen\\LIVE\\loginData.json for LIVE"
        self.assertEqual(
            launcher_log_candidates(text), [Path(r"F:\RSI\StarCitizen\LIVE")]
        )

    def test_ignores_a_lookalike_that_is_not_a_channel(self):
        text = r"F:\\RSI\\StarCitizen\\LIVESTREAM"
        self.assertEqual(launcher_log_candidates(text), [])

    def test_empty_text_yields_nothing(self):
        self.assertEqual(launcher_log_candidates(""), [])


class DefaultLogPathTests(unittest.TestCase):
    """Discovery must not depend on this machine's real install, so the
    hardcoded defaults are patched out in every case here."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(tailer, "_DEFAULT_DIRS", ())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.missing_launcher_log = self.root / "no-such-launcher.log"

    def _install(self, base: Path, parent="Robert Space Industries", channel="LIVE"):
        d = base / parent / "StarCitizen" / channel
        d.mkdir(parents=True, exist_ok=True)
        log = d / "Game.log"
        log.write_text("", encoding="utf-8")
        return log

    def test_falls_back_to_the_default_dirs_first(self):
        expected = self._install(self.root)
        with mock.patch.object(tailer, "_DEFAULT_DIRS", (str(expected.parent),)):
            self.assertEqual(
                tailer.default_log_path(
                    launcher_log=self.missing_launcher_log, roots=[]
                ),
                expected,
            )

    def test_finds_an_install_named_only_in_the_launcher_log(self):
        """The regression this whole change exists for: an install on another
        drive, under no conventional folder name at all."""
        odd = self.root / "somewhere odd"
        expected = self._install(odd)
        launcher_log = self.root / "log.log"
        launcher_log.write_text(
            'installing to "%s"' % str(expected.parent).replace("\\", "\\\\"),
            encoding="utf-8",
        )
        self.assertEqual(
            tailer.default_log_path(launcher_log=launcher_log, roots=[]), expected
        )

    def test_finds_a_conventional_install_on_a_non_c_drive(self):
        """No launcher log at all — the drive-root sweep must still find it."""
        expected = self._install(self.root)
        self.assertEqual(
            tailer.default_log_path(
                launcher_log=self.missing_launcher_log, roots=[self.root]
            ),
            expected,
        )

    def test_finds_a_conventional_install_under_program_files(self):
        expected = self._install(self.root / "Program Files")
        self.assertEqual(
            tailer.default_log_path(
                launcher_log=self.missing_launcher_log, roots=[self.root]
            ),
            expected,
        )

    def test_prefers_live_over_ptu_in_the_drive_sweep(self):
        self._install(self.root, channel="PTU")
        live = self._install(self.root, channel="LIVE")
        self.assertEqual(
            tailer.default_log_path(
                launcher_log=self.missing_launcher_log, roots=[self.root]
            ),
            live,
        )

    def test_returns_none_when_there_is_no_install_anywhere(self):
        """None is the contract: it becomes the 'log not found' subscribe ack,
        which is what prompts the user for a path."""
        self.assertIsNone(
            tailer.default_log_path(
                launcher_log=self.missing_launcher_log, roots=[self.root]
            )
        )

    def test_an_unreadable_launcher_log_is_not_fatal(self):
        """Fail soft, like everything else in the telemetry path: fall through
        to the drive sweep rather than raising out of subscribe."""
        expected = self._install(self.root)
        unreadable = self.root / "locked.log"
        unreadable.write_text("whatever", encoding="utf-8")
        real_read = Path.read_text

        def deny(self_path, *a, **kw):
            if self_path == unreadable:
                raise PermissionError(13, "Permission denied")
            return real_read(self_path, *a, **kw)

        with mock.patch.object(Path, "read_text", deny):
            self.assertEqual(
                tailer.default_log_path(launcher_log=unreadable, roots=[self.root]),
                expected,
            )

    def test_a_launcher_log_naming_a_vanished_install_falls_through(self):
        """The launcher remembers uninstalled/moved paths forever, so a named
        dir that no longer holds a Game.log must not shadow a real one."""
        expected = self._install(self.root)
        launcher_log = self.root / "log.log"
        launcher_log.write_text(
            r"Z:\\Gone\\StarCitizen\\LIVE", encoding="utf-8"
        )
        self.assertEqual(
            tailer.default_log_path(launcher_log=launcher_log, roots=[self.root]),
            expected,
        )


if __name__ == "__main__":
    unittest.main()
