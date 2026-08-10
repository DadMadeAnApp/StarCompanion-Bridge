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


_UPSERTED = re.compile(
    r"<ObjectiveUpserted>.*?mission_id (?P<mission>[0-9a-f-]+)"
    r" - objective_id (?P<objective>[0-9a-f-]+)"
    r" - state MISSION_OBJECTIVE_STATE_(?P<state>\w+)"
    r" - created (?P<created>[01])"
    r" - flags=(?P<flags>[^ ]*)",
    re.DOTALL,
)

# Text runs to the last ']' before the trailing [Team_*] tags, since objective
# text itself contains brackets and parentheses.
_OBJECTIVE_TEXT = re.compile(
    r"<CMissionLogEntry::UpdateActiveObjective> Objective updated id=(?P<objective>[0-9a-f-]+),"
    r" flags=(?P<flags>[^,]*),"
    r".*?hiddenInUI=(?P<hidden_ui>[01]),"
    r".*?uiDisplay\[Priority=\d+\]\[Text=(?P<text>.*?)\](?: \[Team_|$)",
    re.DOTALL,
)


def _split_flags(raw: str) -> list[str]:
    return [f for f in raw.split("|") if f]


def parse_objective_upserted(record: str) -> dict | None:
    m = _UPSERTED.search(record)
    if not m:
        return None
    return {
        "missionId": m.group("mission"),
        "objectiveId": m.group("objective"),
        "state": m.group("state"),
        "created": m.group("created") == "1",
        "flags": _split_flags(m.group("flags")),
    }


def parse_objective_text(record: str) -> dict | None:
    m = _OBJECTIVE_TEXT.search(record)
    if not m:
        return None
    return {
        "objectiveId": m.group("objective"),
        "textRaw": m.group("text"),
        "flags": _split_flags(m.group("flags")),
        "hiddenInUI": m.group("hidden_ui") == "1",
    }


class ObjectiveTable:
    """Joins the two tags that together describe one objective.

    <ObjectiveUpserted> carries state but no text; the text is on a separate
    <CMissionLogEntry::UpdateActiveObjective> line keyed by objective id. Either
    may arrive first, and a half that never completes is never emitted.
    """

    def __init__(self) -> None:
        self._partial: dict[str, dict] = {}

    @staticmethod
    def is_visible(flags: list[str], hidden_in_ui: bool) -> bool:
        """The game's own visibility rules, not advisory.

        'Hidden' is its own flag, distinct from hiddenInUI — do not substring
        match. Roughly a third of objective traffic is internal bookkeeping the
        player is never meant to see.
        """
        if hidden_in_ui or "Hidden" in flags:
            return False
        return "ShowInLog" in flags

    def ingest(self, record: str) -> dict | None:
        """Feed one record. Returns a complete, visible objective or None."""
        upsert = parse_objective_upserted(record)
        if upsert:
            merged = {**self._partial.get(upsert["objectiveId"], {}), **upsert}
        else:
            text = parse_objective_text(record)
            if not text:
                return None
            merged = {**self._partial.get(text["objectiveId"], {}), **text}

        objective_id = merged["objectiveId"]
        if "state" not in merged or "textRaw" not in merged:
            self._partial[objective_id] = merged
            return None

        self._partial.pop(objective_id, None)
        if not self.is_visible(merged.get("flags", []), merged.get("hiddenInUI", False)):
            return None
        return {
            "missionId": merged["missionId"],
            "objectiveId": objective_id,
            "state": merged["state"],
            "created": merged["created"],
            "text": merged["textRaw"],
            "textRaw": merged["textRaw"],
            "resolved": False,
            "flags": merged.get("flags", []),
            "hiddenInUI": merged.get("hiddenInUI", False),
        }
