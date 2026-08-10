"""Pure parsing for Star Citizen's Game.log.

No I/O, no clock, no sockets — every function here is a transform, so the whole
parsing surface is testable on any platform without the game or Windows.

Formats are undocumented and CIG rewrites them freely (the <Actor Death> format
vanished in 4.9), so every parser fails soft: a line that does not match returns
None rather than raising. Verified against Build(12344265), SC 4.9.188.
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator

# A record begins at a timestamp FOLLOWED BY A SEVERITY TOKEN. The timestamp
# alone is not enough: a wrapped notification's continuation carries its own
# timestamp, and the closing '" [N] to queue' and MissionId both live on it, so
# splitting there loses the mission id and truncates the text. Timestamp-less
# continuations exist too (batch/streaming chatter); this one predicate covers
# both forms.
RECORD_START = re.compile(
    r"^<\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z> \[(?:Notice|Warn|Error|Trace)\]"
)


def iter_records(lines: Iterable[str]) -> Iterator[str]:
    """Join continuation lines so each yielded string is one whole record."""
    current: list[str] = []
    for line in lines:
        if RECORD_START.match(line):
            if current:
                yield "".join(current).rstrip("\n")
            current = [line]
        elif current:
            current.append(line)
        # A continuation with no record open is orphaned mid-file; drop it.
    if current:
        yield "".join(current).rstrip("\n")


# DOTALL: the quoted text can contain the newline that wrapped the record.
# Non-greedy text capture (.*?) bounded by the real delimiter "] to queue" to
# prevent cross-notification capture when two notifications share one assembled record.
_NOTIFICATION = re.compile(
    r"<SHUDEvent_OnNotification> Added notification \"(?P<text>.*?)\" \[\d+\] to queue\..*?"
    r"MissionId: \[(?P<mission>[0-9a-f-]+)\]",
    re.DOTALL,
)


def parse_notification(record: str) -> dict | None:
    """HUD notification text, verbatim including any trailing colon."""
    m = _NOTIFICATION.search(record)
    if not m:
        return None
    return {"text": m.group("text"), "missionId": m.group("mission")}
