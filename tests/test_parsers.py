import unittest
from pathlib import Path

from telemetry import parsers

FIXTURE = Path(__file__).parent / "fixtures" / "game_sample.log"


class IterRecordsTests(unittest.TestCase):

    def test_joins_a_timestamped_continuation_into_one_record(self):
        """The real log's wrapped notifications carry a timestamp on the
        continuation line, so a timestamp alone cannot mean 'new record'."""
        lines = [
            '<2026-08-08T02:44:59.361Z> [Notice] <SHUDEvent_OnNotification> Added notification "You have joined channel \'Test Org\'.\n',
            '<2026-08-08T02:44:59.362Z> : " [2] to queue. New queue size: 1, MissionId: [00000000-0000-0000-0000-000000000000], ObjectiveId: []\n',
            "<2026-08-08T02:45:00.000Z> [Notice] <Other> next\n",
        ]
        records = list(parsers.iter_records(lines))
        self.assertEqual(len(records), 2)
        self.assertIn("You have joined", records[0])
        self.assertIn("to queue", records[0])
        self.assertIn("MissionId", records[0])

    def test_joins_a_continuation_that_has_no_timestamp(self):
        lines = [
            "<2026-08-08T02:44:59.361Z> [Notice] <BatchThing> Size:[6] State:[x]\n",
            " LocalBatch:[false] IsChildBatch:[true]\n",
            "<2026-08-08T02:45:00.000Z> [Notice] <Other> next\n",
        ]
        records = list(parsers.iter_records(lines))
        self.assertEqual(len(records), 2)
        self.assertIn("IsChildBatch", records[0])

    def test_a_record_starts_only_at_a_timestamp_and_severity(self):
        lines = ["orphan continuation with no timestamp\n"]
        self.assertEqual(list(parsers.iter_records(lines)), [])

    def test_recognises_every_severity_token(self):
        for severity in ("Notice", "Warn", "Error", "Trace"):
            line = f"<2026-08-08T02:44:59.361Z> [{severity}] <Tag> body\n"
            self.assertEqual(len(list(parsers.iter_records([line]))), 1, severity)


if __name__ == "__main__":
    unittest.main()
