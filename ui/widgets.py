"""The four composite widgets the window is built from."""

from __future__ import annotations

from collections import deque

from ui.log_handler import color_for_level
from ui.theme import BG, DOT_IDLE, FONT_MONO, FONT_UI, MUTED, PANEL, TEXT

# Import tkinter lazily for when it's actually needed; this allows the module
# to be imported without a display so mask() can be tested.
try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = None
    ttk = None


def mask(value: str) -> str:
    """Hide the middle of a secret, keeping enough to confirm it's the right one."""
    if len(value) <= 10:
        return value
    return f"{value[:4]}…{value[-4:]}"


class Tooltip:
    """A hover label. Used to explain why a disabled control is disabled."""

    def __init__(self, widget, text: str) -> None:
        self._widget = widget
        self._text = text
        self._window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self._text = text

    def _show(self, _event=None) -> None:
        if self._window is not None or not self._text:
            return
        x = self._widget.winfo_rootx() + 12
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._window = tk.Toplevel(self._widget)
        self._window.overrideredirect(True)
        self._window.geometry(f"+{x}+{y}")
        tk.Label(
            self._window, text=self._text, background=PANEL, foreground=TEXT,
            font=FONT_UI, padx=8, pady=4, justify="left", wraplength=280,
        ).pack()

    def _hide(self, _event=None) -> None:
        if self._window is not None:
            self._window.destroy()
            self._window = None


class StatusDot(tk.Canvas if tk else object):
    """A filled circle. A Canvas because ttk cannot draw one."""

    def __init__(self, parent, diameter: int = 12) -> None:
        super().__init__(parent, width=diameter, height=diameter,
                         highlightthickness=0, bd=0, bg=BG)
        self._color = DOT_IDLE
        self._oval = self.create_oval(1, 1, diameter - 1, diameter - 1,
                                      fill=DOT_IDLE, outline="")

    def set(self, color: str) -> None:
        self._color = color
        self.itemconfigure(self._oval, fill=color)

    @property
    def color(self) -> str:
        return self._color


class CopyRow(ttk.Frame if ttk else object):
    """label | value | copy, plus an optional reveal toggle for secrets."""

    def __init__(self, parent, label: str, value: str = "", masked: bool = False) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self._value = value
        self._masked = masked
        self._revealed = False

        ttk.Label(self, text=label, style="PanelMuted.TLabel", width=6,
                  anchor="w").grid(row=0, column=0, sticky="w")
        self._value_label = ttk.Label(self, text=self._display_text(),
                                      style="Mono.TLabel", anchor="w")
        self._value_label.grid(row=0, column=1, sticky="ew", padx=(4, 8))
        self.columnconfigure(1, weight=1)

        column = 2
        if masked:
            self._reveal_button = ttk.Button(self, text="Show", width=6,
                                             command=self.toggle_reveal)
            self._reveal_button.grid(row=0, column=column)
            column += 1
        self._copy_button = ttk.Button(self, text="Copy", width=6, command=self.copy)
        self._copy_button.grid(row=0, column=column)

    def _display_text(self) -> str:
        if self._masked and not self._revealed:
            return mask(self._value)
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value
        self._value_label.configure(text=self._display_text())

    @property
    def value(self) -> str:
        return self._value

    @property
    def displayed(self) -> str:
        return self._value_label.cget("text")

    def toggle_reveal(self) -> None:
        self._revealed = not self._revealed
        self._reveal_button.configure(text="Hide" if self._revealed else "Show")
        self._value_label.configure(text=self._display_text())

    def copy(self) -> None:
        # Always the full value, never the masked rendering -- a copy button that
        # yielded "abcd…7890" would be a bug the user only finds while pairing.
        self.clipboard_clear()
        self.clipboard_append(self._value)


class ActivityPane(ttk.Frame if ttk else object):
    """A capped, colour-tagged, auto-scrolling log view."""

    def __init__(self, parent, capacity: int = 500) -> None:
        super().__init__(parent, style="TFrame")
        self._capacity = capacity
        self._lines: deque[str] = deque(maxlen=capacity)
        self._paused = False
        self._missed = 0

        self._text = tk.Text(
            self, height=10, wrap="none", background=PANEL, foreground=TEXT,
            insertbackground=TEXT, font=FONT_MONO, borderwidth=0,
            highlightthickness=0, state="disabled",
        )
        scroll = ttk.Scrollbar(self, orient="vertical", command=self._text.yview,
                               style="Vertical.TScrollbar")
        self._text.configure(yscrollcommand=scroll.set)
        self._text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def lines(self) -> list[str]:
        return list(self._lines)

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        if not self._paused and self._missed:
            plural = "line" if self._missed == 1 else "lines"
            missed, self._missed = self._missed, 0
            self.note(f"… {missed} {plural} while paused")

    def append(self, ts: str, level: int, text: str) -> None:
        if self._paused:
            # The pane is a live view, not the record of truth -- bridge.log has
            # every line regardless, so dropping while paused loses nothing.
            self._missed += 1
            return
        self._write(f"{ts}  {text}", color_for_level(level))

    def note(self, text: str) -> None:
        """A line from the UI itself, not from the log."""
        self._write(text, MUTED)

    def _write(self, line: str, color: str) -> None:
        tag = f"fg{color}"
        if tag not in self._text.tag_names():
            self._text.tag_configure(tag, foreground=color)

        at_bottom = self._text.yview()[1] >= 0.999
        # Detect if the deque is at capacity before append; if so, it will evict
        # the oldest line, and we must delete the corresponding widget line.
        was_at_capacity = len(self._lines) == self._capacity
        self._lines.append(line)
        self._text.configure(state="normal")
        self._text.insert("end", line + "\n", tag)
        # Trim in lockstep with the deque so the widget and `lines` never disagree.
        # When the deque evicts (was at capacity), delete exactly one line from the widget.
        if was_at_capacity:
            self._text.delete("1.0", "2.0")
        self._text.configure(state="disabled")
        # Only follow the tail if the user was already at it; yanking the view
        # back while they are reading history is the classic log-pane annoyance.
        if at_bottom:
            self._text.see("end")

    def clear(self) -> None:
        self._lines.clear()
        self._missed = 0
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")
