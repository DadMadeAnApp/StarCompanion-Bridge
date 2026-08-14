import logging

import pytest

from ui.widgets import ActivityPane, CopyRow, StatusDot, mask


def test_mask_keeps_the_ends_so_a_user_can_confirm_the_right_token():
    assert mask("abcdef1234567890") == "abcd…7890"


def test_mask_leaves_short_values_alone():
    assert mask("abc") == "abc"
    assert mask("") == ""


def test_status_dot_repaints(tk_root):
    dot = StatusDot(tk_root)
    dot.set("#4ade80")
    assert dot.color == "#4ade80"
    dot.set("#f87171")
    assert dot.color == "#f87171"


def test_copy_row_shows_its_value(tk_root):
    row = CopyRow(tk_root, "IP", "192.168.1.42")
    assert row.value == "192.168.1.42"
    assert row.displayed == "192.168.1.42"
    row.set_value("10.0.0.5")
    assert row.displayed == "10.0.0.5"


def test_a_masked_row_hides_the_value_until_revealed(tk_root):
    row = CopyRow(tk_root, "Token", "abcdef1234567890", masked=True)
    assert row.displayed == "abcd…7890"
    row.toggle_reveal()
    assert row.displayed == "abcdef1234567890"
    row.toggle_reveal()
    assert row.displayed == "abcd…7890"


def test_copying_puts_the_full_value_on_the_clipboard_even_when_masked(tk_root):
    row = CopyRow(tk_root, "Token", "abcdef1234567890", masked=True)
    row.copy()
    assert tk_root.clipboard_get() == "abcdef1234567890"


def test_activity_pane_appends_lines(tk_root):
    pane = ActivityPane(tk_root)
    pane.append("21:04:11", logging.INFO, "App connected")
    pane.append("21:04:12", logging.WARNING, "Rate limit exceeded")
    assert pane.lines == ["21:04:11  App connected", "21:04:12  Rate limit exceeded"]


def test_activity_pane_drops_the_oldest_past_capacity(tk_root):
    pane = ActivityPane(tk_root, capacity=3)
    for i in range(5):
        pane.append("21:04:11", logging.INFO, f"line {i}")
    assert len(pane.lines) == 3
    assert pane.lines[0].endswith("line 2")
    assert pane.lines[-1].endswith("line 4")


def test_clear_empties_the_pane(tk_root):
    pane = ActivityPane(tk_root)
    pane.append("21:04:11", logging.INFO, "gone")
    pane.clear()
    assert pane.lines == []


def test_pause_stops_appends_and_resuming_reports_what_was_missed(tk_root):
    pane = ActivityPane(tk_root)
    pane.set_paused(True)
    pane.append("21:04:11", logging.INFO, "while paused")
    assert pane.lines == []
    assert pane.paused is True
    pane.set_paused(False)
    assert pane.lines == ["… 1 line while paused"]


def test_note_writes_a_muted_line(tk_root):
    pane = ActivityPane(tk_root)
    pane.note("… 5 events dropped")
    assert pane.lines == ["… 5 events dropped"]
