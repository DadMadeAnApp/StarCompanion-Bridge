import asyncio
import tempfile
import unittest
from pathlib import Path

from telemetry import tailer


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


if __name__ == "__main__":
    unittest.main()
