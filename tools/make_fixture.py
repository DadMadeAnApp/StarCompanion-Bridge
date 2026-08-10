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
    "<OnDragInventoryItemModifyTarget>",
    "<AttachmentReceived>",
)
HANDLE = "TestPilot"
ACCOUNT = "100000000000"


def redact(line: str) -> str:
    line = re.sub(r"Player\[[^\]]+\]", f"Player[{HANDLE}]", line)
    line = re.sub(r"\b\d{12}\b", ACCOUNT, line)
    # UUIDs keep their shape so parsers still exercise the real format.
    line = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        lambda m: "0" * 8 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + m.group(0)[-12:],
        line,
    )
    return line


def main(src: str, dest: str) -> None:
    lines = Path(src).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    out = lines[:12]  # header block, carries Build(NNNNNNNN)
    kept_chatter = 0
    for i, line in enumerate(lines):
        if any(tag in line for tag in KEEP_TAGS):
            out.append(line)
            # A continuation line has no leading timestamp; carry it along.
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if nxt and not nxt.startswith("<20"):
                out.append(nxt)
        elif any(tag in line for tag in CHATTER_TAGS) and kept_chatter < 40:
            out.append(line)
            kept_chatter += 1
    Path(dest).write_text("".join(redact(x) for x in out), encoding="utf-8")
    print(f"wrote {dest}: {len(out)} lines")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
