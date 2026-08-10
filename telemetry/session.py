"""Per-connection telemetry subscription state.

Subscriptions are per connection, not per install: a client that reconnects must
resubscribe. Kept apart from the socket so it tests without one.
"""
from __future__ import annotations

FEEDS = ("mission", "notification", "context")


class TelemetrySession:

    def __init__(self, game_build: str | None, parser_build: str, log_found: bool = True) -> None:
        self.game_build = game_build
        self.parser_build = parser_build
        self.log_found = log_found
        self.feeds: set[str] = set()
        self._seq = 0

    def subscribe(self, feeds: list[str], req_id: int) -> dict:
        if not self.log_found:
            return {"type": "ack", "ok": False, "id": req_id, "error": "log not found"}
        unknown = [f for f in feeds if f not in FEEDS]
        if unknown:
            return {"type": "ack", "ok": False, "id": req_id,
                    "error": f"unknown feeds: {', '.join(unknown)}"}
        self.feeds = set(feeds)
        return {
            "type": "ack", "ok": True, "id": req_id, "feeds": list(feeds),
            "gameBuild": self.game_build, "parserBuild": self.parser_build,
        }

    def unsubscribe(self, req_id: int) -> dict:
        self.feeds = set()
        return {"type": "ack", "ok": True, "id": req_id}

    def next_frame(self, feed: str, event: dict, ts: str) -> dict | None:
        """Build one telemetry frame, or None when the feed isn't subscribed.

        Telemetry frames carry no "id" — they are unsolicited and must never be
        acked by the client.
        """
        if feed not in self.feeds:
            return None
        frame = {"type": "telemetry", "feed": feed, "seq": self._seq, "ts": ts, "event": event}
        self._seq += 1
        return frame
