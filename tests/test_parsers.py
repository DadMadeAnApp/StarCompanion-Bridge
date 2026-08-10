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


class NotificationTests(unittest.TestCase):

    def test_parses_text_and_mission_id(self):
        record = (
            '<2026-08-08T02:48:33.955Z> [Notice] <SHUDEvent_OnNotification> Added notification '
            '"Contract Accepted:  Reclusive Bounty: Sergio Lesnik | High Risk: " [6] to queue. '
            "New queue size: 1, MissionId: [e1aab39d-24d1-4ece-8cea-cf527382976c], ObjectiveId: [] "
            "[Team_CoreGameplayFeatures][Missions][Comms]"
        )
        event = parsers.parse_notification(record)
        self.assertEqual(event["text"], "Contract Accepted:  Reclusive Bounty: Sergio Lesnik | High Risk: ")
        self.assertEqual(event["missionId"], "e1aab39d-24d1-4ece-8cea-cf527382976c")

    def test_parses_a_wrapped_notification(self):
        record = (
            '<2026-08-08T02:44:59.361Z> [Notice] <SHUDEvent_OnNotification> Added notification '
            '"You have joined channel \'Aegis Redeemer : Firelance\'.\n'
            '" [4] to queue. New queue size: 1, MissionId: [00000000-0000-0000-0000-000000000000], ObjectiveId: []'
        )
        event = parsers.parse_notification(record)
        self.assertIn("Aegis Redeemer", event["text"])
        self.assertEqual(event["missionId"], "00000000-0000-0000-0000-000000000000")

    def test_returns_none_for_an_unrelated_record(self):
        self.assertIsNone(parsers.parse_notification("<2026-08-08T02:44:59.361Z> [Notice] <Other> hi"))

    def test_returns_none_for_a_malformed_notification(self):
        record = "<2026-08-08T02:44:59.361Z> [Notice] <SHUDEvent_OnNotification> Added notification but truncated"
        self.assertIsNone(parsers.parse_notification(record))

    def test_two_notifications_in_one_record_returns_the_first(self):
        record = (
            '<2026-08-08T02:44:59.361Z> [Notice] <SHUDEvent_OnNotification> Added notification '
            '"First" [1] to queue. New queue size: 1, MissionId: [11111111-1111-1111-1111-111111111111], ObjectiveId: []\n'
            '<2026-08-08T02:44:59.362Z> [Notice] <SHUDEvent_OnNotification> Added notification '
            '"Second" [2] to queue. New queue size: 2, MissionId: [22222222-2222-2222-2222-222222222222], ObjectiveId: []'
        )
        event = parsers.parse_notification(record)
        self.assertEqual(event["text"], "First")
        self.assertEqual(event["missionId"], "11111111-1111-1111-1111-111111111111")


if __name__ == "__main__":
    unittest.main()
