import unittest

from telemetry.session import TelemetrySession


class SubscribeTests(unittest.TestCase):

    def test_ack_echoes_the_feeds_and_reports_builds(self):
        session = TelemetrySession(game_build="12344265", parser_build="12344265")
        ack = session.subscribe(["mission", "notification", "context"], req_id=42)
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["id"], 42)
        self.assertEqual(ack["feeds"], ["mission", "notification", "context"])
        self.assertEqual(ack["gameBuild"], "12344265")
        self.assertEqual(ack["parserBuild"], "12344265")

    def test_unknown_feeds_are_rejected(self):
        session = TelemetrySession(game_build="1", parser_build="1")
        ack = session.subscribe(["mission", "inventory"], req_id=7)
        self.assertFalse(ack["ok"])
        self.assertIn("inventory", ack["error"])

    def test_missing_log_reports_the_documented_error(self):
        session = TelemetrySession(game_build=None, parser_build="1", log_found=False)
        ack = session.subscribe(["mission"], req_id=1)
        self.assertFalse(ack["ok"])
        self.assertEqual(ack["error"], "log not found")


class FrameTests(unittest.TestCase):

    def setUp(self):
        self.session = TelemetrySession(game_build="1", parser_build="1")
        self.session.subscribe(["mission", "notification"], req_id=1)

    def test_seq_starts_at_zero_and_increments(self):
        first = self.session.next_frame("mission", {"text": "a"}, ts="2026-08-08T02:49:20.967Z")
        second = self.session.next_frame("mission", {"text": "b"}, ts="2026-08-08T02:49:21.967Z")
        self.assertEqual(first["seq"], 0)
        self.assertEqual(second["seq"], 1)

    def test_frame_shape_matches_the_wire_spec(self):
        frame = self.session.next_frame("mission", {"text": "a"}, ts="2026-08-08T02:49:20.967Z")
        self.assertEqual(frame["type"], "telemetry")
        self.assertEqual(frame["feed"], "mission")
        self.assertEqual(frame["ts"], "2026-08-08T02:49:20.967Z")
        self.assertEqual(frame["event"], {"text": "a"})
        self.assertNotIn("id", frame)

    def test_unsubscribed_feeds_produce_no_frame(self):
        self.assertIsNone(self.session.next_frame("context", {"location": "X"}, ts="t"))

    def test_unsubscribe_stops_every_feed(self):
        self.session.unsubscribe(req_id=9)
        self.assertIsNone(self.session.next_frame("mission", {"text": "a"}, ts="t"))

    def test_dropped_frames_still_burn_a_seq_so_the_client_sees_a_gap(self):
        # Mirrors the pump's ordering: build the frame (advancing seq) first,
        # then decide whether to actually deliver it. A frame that is built
        # but not delivered must still consume a sequence number, so the
        # client can detect the drop purely from a gap in the numbers it
        # receives (TELEMETRY_HANDOFF.md §6.3) rather than silently rendering
        # an incomplete-but-believed-complete journal.
        sent_seqs = []
        for i in range(5):
            frame = self.session.next_frame("mission", {"text": str(i)}, ts="t")
            deliver = i % 2 == 0  # simulate the rate limiter dropping every other frame
            if deliver:
                sent_seqs.append(frame["seq"])

        self.assertGreater(len(sent_seqs), 1)
        gaps = [b - a for a, b in zip(sent_seqs, sent_seqs[1:])]
        self.assertTrue(any(gap > 1 for gap in gaps))


if __name__ == "__main__":
    unittest.main()
