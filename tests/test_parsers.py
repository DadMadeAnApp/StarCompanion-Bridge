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

    def test_recognises_every_bracket_prefix_the_log_emits(self):
        for prefix in ("[Notice]", "[Warn]", "[Error]", "[Trace]",
                       "[SPAM 135][Error]", "[SPAM 99][Notice]", "[CVARS]",
                       "[flow]", "[VK_INFO]", "[CGlobalGameUI::OpenLoadingScreen]"):
            line = f"<2026-08-08T02:44:59.361Z> {prefix} <Tag> body\n"
            self.assertEqual(len(list(parsers.iter_records([line]))), 1, prefix)

    def test_a_spam_line_does_not_glue_onto_the_record_before_it(self):
        lines = [
            "<2026-08-08T03:28:58.406Z> [Notice] <ObjectiveUpserted> real record\n",
            "<2026-08-08T03:29:02.041Z> [SPAM 135][Error] <Fire Simulation> Radiation Heat\n",
        ]
        records = list(parsers.iter_records(lines))
        self.assertEqual(len(records), 2)
        self.assertNotIn("Radiation Heat", records[0])


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


UPSERT = (
    "<2026-08-08T02:49:20.967Z> [Notice] <ObjectiveUpserted> Received ObjectiveUpserted push "
    "message for: mission_id 94c24d22-cf1f-4e79-bdce-b35f3ee499c0 - objective_id "
    "04430bfc-cf39-c4e1-84c8-7203dacbc9a7 - state MISSION_OBJECTIVE_STATE_INPROGRESS - created 1 "
    "- flags=ShowInLog|RespectInheritedVisibility| [Team_GameServices][Missions]"
)
TEXT = (
    "<2026-08-08T02:49:53.697Z> [Notice] <CMissionLogEntry::UpdateActiveObjective> Objective "
    "updated id=04430bfc-cf39-c4e1-84c8-7203dacbc9a7, flags=ShowInLog|LocalOnlyObjective|, "
    "hidden=0, hiddenInUI=0, markerHidden=0, uiDisplay[Priority=1][Text=Go to ~mission(Location)] "
    "[Team_MissionFeatures][Missions]"
)


class ObjectiveTests(unittest.TestCase):

    def test_parses_the_upsert(self):
        event = parsers.parse_objective_upserted(UPSERT)
        self.assertEqual(event["missionId"], "94c24d22-cf1f-4e79-bdce-b35f3ee499c0")
        self.assertEqual(event["objectiveId"], "04430bfc-cf39-c4e1-84c8-7203dacbc9a7")
        self.assertEqual(event["state"], "INPROGRESS")
        self.assertTrue(event["created"])
        self.assertEqual(event["flags"], ["ShowInLog", "RespectInheritedVisibility"])

    def test_parses_the_text_line(self):
        event = parsers.parse_objective_text(TEXT)
        self.assertEqual(event["objectiveId"], "04430bfc-cf39-c4e1-84c8-7203dacbc9a7")
        self.assertEqual(event["textRaw"], "Go to ~mission(Location)")
        self.assertFalse(event["hiddenInUI"])

    def test_keeps_printf_placeholders_verbatim(self):
        record = TEXT.replace("Go to ~mission(Location)", "Waves Defeated: %ls")
        self.assertEqual(parsers.parse_objective_text(record)["textRaw"], "Waves Defeated: %ls")

    def test_join_emits_only_when_both_halves_have_arrived(self):
        table = parsers.ObjectiveTable()
        self.assertIsNone(table.ingest(UPSERT))
        event = table.ingest(TEXT)
        self.assertIsNotNone(event)
        self.assertEqual(event["missionId"], "94c24d22-cf1f-4e79-bdce-b35f3ee499c0")
        self.assertEqual(event["textRaw"], "Go to ~mission(Location)")
        self.assertEqual(event["state"], "INPROGRESS")

    def test_join_works_when_the_text_line_arrives_first(self):
        table = parsers.ObjectiveTable()
        self.assertIsNone(table.ingest(TEXT))
        self.assertIsNotNone(table.ingest(UPSERT))

    def test_hidden_is_distinct_from_hidden_in_ui(self):
        self.assertFalse(parsers.ObjectiveTable.is_visible(["Hidden", "RespectInheritedVisibility"], False))
        self.assertFalse(parsers.ObjectiveTable.is_visible(["RespectInheritedVisibility"], False))
        self.assertTrue(parsers.ObjectiveTable.is_visible(["ShowInLog"], False))
        self.assertFalse(parsers.ObjectiveTable.is_visible(["ShowInLog"], True))

    def test_invisible_objectives_are_never_emitted(self):
        table = parsers.ObjectiveTable()
        hidden_upsert = UPSERT.replace("flags=ShowInLog|", "flags=Hidden|")
        table.ingest(hidden_upsert)
        self.assertIsNone(table.ingest(TEXT.replace("flags=ShowInLog|", "flags=Hidden|")))

    def test_emitted_event_is_order_independent(self):
        table_upsert_first = parsers.ObjectiveTable()
        table_upsert_first.ingest(UPSERT)
        event_upsert_first = table_upsert_first.ingest(TEXT)

        table_text_first = parsers.ObjectiveTable()
        table_text_first.ingest(TEXT)
        event_text_first = table_text_first.ingest(UPSERT)

        self.assertIsNotNone(event_upsert_first)
        self.assertEqual(event_upsert_first, event_text_first)

    def test_hidden_in_either_half_suppresses_regardless_of_order(self):
        hidden_text = TEXT.replace("flags=ShowInLog|", "flags=Hidden|")

        table_upsert_first = parsers.ObjectiveTable()
        table_upsert_first.ingest(UPSERT)
        self.assertIsNone(table_upsert_first.ingest(hidden_text))

        table_text_first = parsers.ObjectiveTable()
        table_text_first.ingest(hidden_text)
        self.assertIsNone(table_text_first.ingest(UPSERT))

    def test_partial_table_is_capped_and_evicts_oldest(self):
        table = parsers.ObjectiveTable()
        for i in range(600):
            objective_id = f"{i:08x}-0000-0000-0000-000000000000"
            record = UPSERT.replace(
                "04430bfc-cf39-c4e1-84c8-7203dacbc9a7", objective_id
            )
            table.ingest(record)
        self.assertEqual(len(table._partial), 512)

        # The very first objective's upsert half was evicted, so its matching
        # text arriving now cannot complete a pair - it just starts a new one.
        evicted_id = "00000000-0000-0000-0000-000000000000"
        evicted_text = TEXT.replace(
            "04430bfc-cf39-c4e1-84c8-7203dacbc9a7", evicted_id
        )
        self.assertIsNone(table.ingest(evicted_text))


class LocationTests(unittest.TestCase):

    def test_parses_the_location_code(self):
        record = (
            "<2026-08-08T02:43:46.205Z> [Notice] <RequestLocationInventory> Player[TestPilot] "
            "requested inventory for Location[Stanton4_NewBabbage] [Team_CoreGameplayFeatures][Inventory]"
        )
        self.assertEqual(parsers.parse_location(record), {"location": "Stanton4_NewBabbage"})

    def test_does_not_emit_the_handle(self):
        record = (
            "<2026-08-08T02:43:46.205Z> [Notice] <RequestLocationInventory> Player[TestPilot] "
            "requested inventory for Location[RR_MIC_LEO] [Team_CoreGameplayFeatures][Inventory]"
        )
        self.assertNotIn("TestPilot", str(parsers.parse_location(record)))


class BuildTests(unittest.TestCase):

    def test_reads_the_build_from_the_header(self):
        header = '<2026-08-08T02:42:26.205Z> BackupNameAttachment=" Build(12344265) 07 Aug 26 (22 42 19)"'
        self.assertEqual(parsers.parse_build(header), "12344265")

    def test_returns_none_for_any_other_line(self):
        self.assertIsNone(parsers.parse_build("<2026-08-08T02:42:26.206Z> Running 64 bit version"))


class FeedRouterTests(unittest.TestCase):

    def test_routes_each_whitelisted_tag_to_its_feed(self):
        router = parsers.FeedRouter()
        router.route(UPSERT)
        feed, event = router.route(TEXT)
        self.assertEqual(feed, "mission")
        location = (
            "<2026-08-08T02:43:46.205Z> [Notice] <RequestLocationInventory> Player[TestPilot] "
            "requested inventory for Location[Stanton4_NewBabbage]"
        )
        self.assertEqual(router.route(location)[0], "context")

    def test_drops_every_unwhitelisted_tag(self):
        router = parsers.FeedRouter()
        self.assertIsNone(router.route("<2026-08-08T02:43:46.205Z> [Notice] <OnDragInventoryItemModifyTarget> noise"))

    def test_survives_the_whole_fixture_without_raising(self):
        router = parsers.FeedRouter()
        counts = {"mission": 0, "notification": 0, "context": 0}
        with FIXTURE.open(encoding="utf-8", errors="replace") as fh:
            for record in parsers.iter_records(fh):
                routed = router.route(record)
                if routed:
                    counts[routed[0]] += 1
        self.assertGreater(counts["mission"], 0)
        self.assertGreater(counts["notification"], 0)
        self.assertGreater(counts["context"], 0)

    def test_noise_lines_do_not_leak_into_emitted_events(self):
        """Verify that trailing noise lines (e.g., [SPAM], [CVARS]) captured
        by the fixture do not corrupt mission/notification/context event text."""
        router = parsers.FeedRouter()
        with FIXTURE.open(encoding="utf-8", errors="replace") as fh:
            for record in parsers.iter_records(fh):
                routed = router.route(record)
                if routed:
                    feed, event = routed
                    # Check that event text (if present) does not contain noise markers
                    if "text" in event:
                        self.assertNotIn("SPAM", event["text"],
                            f"SPAM noise leaked into {feed} event text: {event['text'][:100]}")
                        self.assertNotIn("Radiation Heat", event["text"],
                            f"Radiation Heat noise leaked into {feed} event text: {event['text'][:100]}")


if __name__ == "__main__":
    unittest.main()
