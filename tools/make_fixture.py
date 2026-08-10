"""Build a redacted test fixture from a real Game.log.

Usage: python3 tools/make_fixture.py /path/to/Game.log tests/fixtures/game_sample.log

Keeps the header (needed for the build string), every line whose tag is in the
telemetry whitelist, and a sample of chatter so tests prove the whitelist drops
it. Rewrites the handle, account id, and every UUID so the fixture carries no
real identifiers.
"""
import re
import sys
from pathlib import Path

KEEP_TAGS = (
    "<ObjectiveUpserted>",
    "<CMissionLogEntry::UpdateActiveObjective>",
    "<SHUDEvent_OnNotification>",
    "<RequestLocationInventory>",
)
CHATTER_TAGS = (
    "<InventoryManagementRequest>",
)
HANDLE = "TestPilot"
ACCOUNT = "100000000000"


def make_redact(uuid_map: dict) -> callable:
    """Factory that returns a redact function with UUID mapping state."""
    def redact(line: str) -> str:
        # Redact player handle (in Player[...] and quoted contexts)
        line = re.sub(r"Player\[[^\]]+\]", f"Player[{HANDLE}]", line)
        line = re.sub(r"'MrDadpool'", f"'{HANDLE}'", line)

        # Redact account ID: word-bounded 12-digit sequences
        line = re.sub(r"\b\d{12}\b", ACCOUNT, line)

        # Redact UUIDs with deterministic mapping first (preserve format for parsers)
        # Must run before digit substitution to avoid mangling UUID internals
        def redact_uuid(m):
            uuid = m.group(0)
            if uuid not in uuid_map:
                uuid_map[uuid] = len(uuid_map)
            n = uuid_map[uuid]
            return f"00000000-0000-0000-0000-{n:012d}"
        line = re.sub(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            redact_uuid,
            line,
        )

        # Redact entity IDs: any run of 9+ digits (catches IDs in item names)
        # Avoid matching digit sequences inside UUID patterns (those between hyphens)
        def redact_digits(m):
            return "100000000000"[:len(m.group(0))]
        # Match 9+ digits that are NOT surrounded by hyphens (not inside UUIDs)
        line = re.sub(r"(?<![0-9a-f-])\d{9,}(?![0-9a-f-])", redact_digits, line)

        # Redact org/channel names in notifications
        line = re.sub(
            r"(channel ['\"])[^'\"]+(['\"])",
            r"\1Test Ship : TestOrg\2",
            line,
        )

        # Redact org names that appear in message content (e.g., "Firelance has left the channel")
        line = re.sub(r"\bFirelance\b", "TestOrg", line)

        return line
    return redact


def main(src: str, dest: str) -> None:
    lines = Path(src).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    out = lines[:12]  # header block, carries Build(NNNNNNNN)
    kept_chatter = 0
    skip_until = 12  # Track line indices we've already processed
    wrapped_count = 0  # Track wrapped records for reporting

    for i in range(12, len(lines)):
        if i < skip_until:
            # Already processed as a continuation of an earlier line
            continue

        line = lines[i]

        if any(tag in line for tag in KEEP_TAGS):
            out.append(line)
            # Consume all continuation lines that follow
            # A continuation line either:
            # 1. Doesn't start with a timestamp at all (indented), OR
            # 2. Has a timestamp but no [LEVEL] marker (no "> [")
            j = i + 1
            continuation_count = 0
            while j < len(lines):
                nxt = lines[j]
                # Check if this is a new log record (starts with timestamp + [LEVEL])
                if re.search(r"> \[", nxt):
                    # New record, stop consuming continuations
                    break
                # If it doesn't start with <20, it's a continuation without timestamp
                if not re.match(r"^<20\d{2}", nxt):
                    out.append(nxt)
                    continuation_count += 1
                    j += 1
                else:
                    # Has a timestamp but no [LEVEL], also a continuation
                    out.append(nxt)
                    continuation_count += 1
                    j += 1
            if continuation_count > 0:
                wrapped_count += 1
            skip_until = j

        elif any(tag in line for tag in CHATTER_TAGS) and kept_chatter < 40:
            out.append(line)
            # Also consume continuations for chatter lines
            j = i + 1
            continuation_count = 0
            while j < len(lines):
                nxt = lines[j]
                # Check if this is a new log record (has "> [" after timestamp)
                if re.search(r"> \[", nxt):
                    # New record, stop consuming continuations
                    break
                # Otherwise, it's a continuation (timestamp-less or timestamp without [LEVEL])
                out.append(nxt)
                continuation_count += 1
                j += 1
            if continuation_count > 0:
                wrapped_count += 1
            skip_until = j
            kept_chatter += 1

    # Add a synthetic wrapped record with a continuation that has no timestamp.
    # The genuine SHUDEvent_OnNotification records in the window all have timestamp
    # continuations (with the same time but no [LEVEL] marker), but Task 2 and Task 6
    # tests need at least one record where the continuation is completely timestamp-less
    # to exercise the full assembly logic. This is synthetic and serves the test.
    # Must be complete and parseable by parse_notification().
    synthetic_record = [
        f'<2026-08-08T03:21:45.123Z> [Notice] <SHUDEvent_OnNotification> Added notification "Test wrapped\n',
        f' message that spans two lines" [1] to queue. New queue size: 1, MissionId: [99999999-9999-9999-9999-999999999999], ObjectiveId: []\n',
    ]
    out.extend(synthetic_record)

    # Create the redact function with UUID mapping
    uuid_map = {}
    redact = make_redact(uuid_map)

    Path(dest).write_text("".join(redact(x) for x in out), encoding="utf-8")
    print(f"wrote {dest}: {len(out)} lines")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
