"""Pure parsing for Star Citizen's Game.log.

No I/O, no clock, no sockets — every function here is a transform, so the whole
parsing surface is testable on any platform without the game or Windows.

Formats are undocumented and CIG rewrites them freely (the <Actor Death> format
vanished in 4.9), so every parser fails soft: a line that does not match returns
None rather than raising.

Build(12344265) / SC 4.9.188 note, 2026-08-13, from a real session: BOTH halves of
an objective are logged INCONSISTENTLY, and neither can be relied on alone.

- <ObjectiveUpserted> (state + flags) is a *server push* — the log says so. Two
  contracts were accepted an hour apart and only the first got one.
- <CMissionLogEntry::UpdateActiveObjective> (uiDisplay text) exists but is rarer
  still: absent for both of those contracts, present for a later phase-driven
  mission. It was first believed removed in this build, on the strength of a
  session where it never fired — including with the in-game mission log open.
  It is not removed; it is just not emitted for every objective.
- The ObjectiveId-bearing <SHUDEvent_OnNotification> is the most consistent
  source, appearing for every objective observed, and is the only text source
  for the two that had no uiDisplay line.

So all three feed ObjectiveTable and whichever arrives first wins. Text quality
differs by source: uiDisplay gives "Confirm Status of Missing Person", while the
notification gives "…Person: " — the trailing ': ' is the game's stripped %ls,
kept deliberately (see strip_objective_prefix).

The practical consequence for the app is that a *complete* objective list cannot
be built from this log: visible objectives are never observed reaching COMPLETED
(only hidden internal 'phase_…' entries are), so entries accumulate as
permanently in-progress. That is why the client's Objectives section was cut on
2026-08-13 — see the module-journal skill. The parsing here is still correct and
still used by the mission feed.
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator

# A record begins at a timestamp FOLLOWED BY ANY '[' bracket group. The
# timestamp alone is not enough: a wrapped notification's continuation
# carries its own timestamp, and the closing '" [N] to queue' and MissionId
# both live on it, so splitting there loses the mission id and truncates the
# text. Timestamp-less continuations exist too (batch/streaming chatter);
# this one predicate covers both forms.
#
# A fixed severity whitelist (Notice/Warn/Error/Trace) is wrong: the real log
# also opens records with '[SPAM N][Notice]', '[SPAM N][Error]', '[CVARS]',
# '[flow]', '[VK_INFO]', '[CGlobalGameUI::OpenLoadingScreen]', and other
# bracket prefixes the whitelist doesn't know about. Any one of those that
# doesn't match gets treated as a continuation and glued onto the record
# before it — e.g. an unrelated '[SPAM 135][Error]' line landing inside an
# <ObjectiveUpserted> record's text. Genuine continuations never start with
# a bracket at all: they look like '<ts> : " [2] to queue…' or
# '<ts>    "You have joined channel…', so matching on the bracket alone,
# regardless of what's inside it, distinguishes a new record from a
# continuation without needing to enumerate every prefix CIG might emit.
# Header lines ('<ts> BackupNameAttachment="…"') have no bracket either and
# stay continuations, which is fine — parse_build reads them as raw lines.
RECORD_START = re.compile(
    r"^<\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z> \["
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
#
# Both the text capture and the gap before MissionId use
# `(?:(?!Added notification).)*?` rather than a plain `.*?`. A plain
# non-greedy dot is only non-greedy about how FAR it reaches, not about
# WHERE it's allowed to reach: when a first notification has no MissionId
# reachable before hitting a second "Added notification" block, the engine
# backtracks and lets that first `.*?` (the text group itself) swallow the
# second notification's own opening text and delimiter whole, then matches
# the second's MissionId as if it were the first's -- pairing the wrong text
# with the wrong id, worse than before. Barring "Added notification" from
# every span between the outer markers means a record with two notifications
# either binds each strictly to its own MissionId, or -- if a MissionId is
# genuinely missing for the one in front -- fails to match that one at all
# and falls through to matching the next, rather than silently cross-binding.
_NOTIFICATION = re.compile(
    r"<SHUDEvent_OnNotification> Added notification \""
    r"(?P<text>(?:(?!Added notification).)*?)"
    r"\" \[\d+\] to queue\."
    r"(?:(?!Added notification).)*?"
    r"MissionId: \[(?P<mission>[0-9a-f-]+)\]"
    # ObjectiveId sits immediately after MissionId on the same line and is the
    # empty "[]" for anything not tied to an objective (system chatter, and the
    # "Contract Accepted: …" mission-title notification). Optional so a build
    # that omits the field entirely still parses text+mission.
    r"(?:, ObjectiveId: \[(?P<objective>[0-9a-f-]*)\])?",
    re.DOTALL,
)

# The notification that carries an objective's text prefixes it with this HUD
# framing. Stripped for the objective row (where it is the objective's own text)
# but never for the notification itself, which stays verbatim.
OBJECTIVE_NOTIFICATION_PREFIX = "New Objective: "


def parse_notification(record: str) -> dict | None:
    """HUD notification text, verbatim including any trailing colon.

    `objectiveId` is None unless this notification is tied to an objective; on
    Build(12344265) that pairing is the only source of objective text.
    """
    m = _NOTIFICATION.search(record)
    if not m:
        return None
    objective = m.group("objective") or None
    return {
        "text": m.group("text"),
        "missionId": m.group("mission"),
        "objectiveId": objective,
    }


def strip_objective_prefix(text: str) -> str:
    """Drop the 'New Objective: ' HUD framing, keeping everything else.

    The trailing ': ' left by the game's stripped %ls placeholder stays — it is
    documented as rendering literally, and inventing a value the game never
    logged would be worse than a stray colon.
    """
    if text.startswith(OBJECTIVE_NOTIFICATION_PREFIX):
        return text[len(OBJECTIVE_NOTIFICATION_PREFIX):]
    return text


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


_LOCATION = re.compile(r"<RequestLocationInventory>.*?Location\[(?P<location>[^\]]+)\]", re.DOTALL)
_BUILD = re.compile(r"Build\((?P<build>\d+)\)")

# Whitelist, never blacklist: a new patch's new chatter tag is far more likely
# to be noise than signal, so anything unlisted is dropped unread.
TAG_MISSION_UPSERT = "<ObjectiveUpserted>"
TAG_MISSION_TEXT = "<CMissionLogEntry::UpdateActiveObjective>"
TAG_NOTIFICATION = "<SHUDEvent_OnNotification>"
TAG_LOCATION = "<RequestLocationInventory>"
WHITELIST = (TAG_MISSION_UPSERT, TAG_MISSION_TEXT, TAG_NOTIFICATION, TAG_LOCATION)


def parse_location(record: str) -> dict | None:
    """Current location code.

    The player handle in this line is deliberately not emitted — no feed in this
    build needs it, and the smallest payload is the safest one.
    """
    m = _LOCATION.search(record)
    return {"location": m.group("location")} if m else None


def parse_build(record: str) -> str | None:
    """The game build from the log header, for the parser/game build advisory."""
    if "BackupNameAttachment" not in record:
        return None
    m = _BUILD.search(record)
    return m.group("build") if m else None


_PARTIAL_CAP = 512


class ObjectiveTable:
    """Joins the two tags that together describe one objective.

    <ObjectiveUpserted> carries state but no text; the text is on a separate
    <CMissionLogEntry::UpdateActiveObjective> line keyed by objective id. Either
    may arrive first, and a half that never completes is never emitted.

    Both halves carry their own `flags`/`hiddenInUI`, and they can disagree.
    Rather than let whichever half arrives second silently overwrite the
    other's visibility signal, the two are unioned and 'hidden' is sticky:
    once either half says hidden, later data can never revive it.

    On Build(12344265) the text half often does not arrive as its own record at
    all (see the module docstring); the HUD notification carrying a matching
    ObjectiveId supplies it instead, fed in via `ingest_notification_text`. Both
    sources land in the same merge, so ordering and visibility behave identically
    whichever supplies the text, and whichever arrives first wins.

    Emitted text is also remembered per objective id, because a later state
    change (INPROGRESS → COMPLETED) arrives as a bare <ObjectiveUpserted> with
    no notification beside it. Without the cache that update would have no text,
    fail the completeness check, and silently never emit — the same class of
    bug as the one that made this whole feed empty.

    `_partial` and `_texts` are each bounded to `_PARTIAL_CAP` entries
    (oldest-inserted evicted first) so a long session cannot grow this table
    without bound. No clock/TTL — this module stays I/O-free.
    """

    def __init__(self) -> None:
        self._partial: dict[str, dict] = {}
        self._texts: dict[str, str] = {}

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

    def _remember_text(self, objective_id: str, text: str) -> None:
        # Pop before re-inserting so insertion order stays "least recently
        # seen first", which is what the eviction below relies on.
        self._texts.pop(objective_id, None)
        if len(self._texts) >= _PARTIAL_CAP:
            self._texts.pop(next(iter(self._texts)))
        self._texts[objective_id] = text

    def ingest(self, record: str) -> dict | None:
        """Feed one record. Returns a complete, visible objective or None."""
        upsert = parse_objective_upserted(record)
        is_upsert = upsert is not None
        if is_upsert:
            new_half = upsert
        else:
            new_half = parse_objective_text(record)
            if not new_half:
                return None
        return self._merge(new_half, is_upsert)

    def ingest_notification_text(self, objective_id: str, text_raw: str) -> dict | None:
        """Text half sourced from a HUD notification's ObjectiveId pairing.

        Contributes no flags and no hiddenInUI: a notification carries neither,
        so visibility is decided entirely by the <ObjectiveUpserted> half. That
        is the safe direction — an objective with no upsert never becomes
        visible on the strength of a notification alone.
        """
        return self._merge(
            {
                "objectiveId": objective_id,
                "textRaw": text_raw,
                "flags": [],
                "hiddenInUI": False,
            },
            is_upsert=False,
        )

    def _merge(self, new_half: dict, is_upsert: bool) -> dict | None:
        objective_id = new_half["objectiveId"]
        existing = self._partial.get(objective_id, {})

        merged = {**existing, **new_half}
        # Keep each half's raw flags/hiddenInUI keyed by half, not by arrival
        # order, so the union below always combines them in the same
        # (upsert, text) order and the emitted event is identical regardless
        # of which half arrived first.
        if is_upsert:
            merged["_upsertFlags"] = new_half.get("flags", [])
            merged["_upsertHiddenInUI"] = new_half.get("hiddenInUI", False)
            merged.setdefault("_textFlags", existing.get("_textFlags", []))
            merged.setdefault("_textHiddenInUI", existing.get("_textHiddenInUI", False))
        else:
            merged["_textFlags"] = new_half.get("flags", [])
            merged["_textHiddenInUI"] = new_half.get("hiddenInUI", False)
            merged.setdefault("_upsertFlags", existing.get("_upsertFlags", []))
            merged.setdefault("_upsertHiddenInUI", existing.get("_upsertHiddenInUI", False))

        merged["flags"] = list(
            dict.fromkeys([*merged["_upsertFlags"], *merged["_textFlags"]])
        )
        merged["hiddenInUI"] = merged["_upsertHiddenInUI"] or merged["_textHiddenInUI"]

        # Remember text as soon as it is known, and reuse it for a later
        # state-change upsert that arrives with no text beside it.
        if "textRaw" in merged:
            self._remember_text(objective_id, merged["textRaw"])
        elif objective_id in self._texts:
            merged["textRaw"] = self._texts[objective_id]

        if "state" not in merged or "textRaw" not in merged:
            # Pop before re-inserting: dict insertion order is what backs the
            # "oldest first" eviction below, so an update to an existing key
            # must move it to the end (most recent) rather than leaving it at
            # its original insertion position. Without this a long-lived,
            # frequently-updated half is evicted ahead of a newer, dead one
            # purely because it was first ingested earlier.
            self._partial.pop(objective_id, None)
            if len(self._partial) >= _PARTIAL_CAP:
                oldest = next(iter(self._partial))
                self._partial.pop(oldest)
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


class FeedRouter:
    """Whitelist dispatch: record in, list of (feed, event) out.

    Returns a LIST, not a single pair: one notification record can legitimately
    produce two events. An objective-bearing notification is both a real HUD
    notification (its own feed, text verbatim) and the objective's text half
    (completing a mission event). Collapsing that to one would mean choosing
    between a missing notification and a permanently empty objective list.
    """

    def __init__(self) -> None:
        self._objectives = ObjectiveTable()

    def route(self, record: str) -> list[tuple[str, dict]]:
        if TAG_MISSION_UPSERT in record or TAG_MISSION_TEXT in record:
            event = self._objectives.ingest(record)
            return [("mission", event)] if event else []
        if TAG_NOTIFICATION in record:
            parsed = parse_notification(record)
            if not parsed:
                return []
            # The wire event stays {text, missionId}: objectiveId is used here
            # to pair the halves and is deliberately not published, keeping the
            # smallest payload (same reasoning as parse_location's player handle).
            out: list[tuple[str, dict]] = [
                ("notification", {"text": parsed["text"], "missionId": parsed["missionId"]})
            ]
            objective_id = parsed["objectiveId"]
            if objective_id:
                mission = self._objectives.ingest_notification_text(
                    objective_id, strip_objective_prefix(parsed["text"])
                )
                if mission:
                    out.append(("mission", mission))
            return out
        if TAG_LOCATION in record:
            event = parse_location(record)
            return [("context", event)] if event else []
        return []
