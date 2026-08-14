"""Palette, fonts, ttk styling, and the QR bitmap.

ttk's default theme ignores most colour options on most platforms, so the styles
below are built on the "clam" theme, which honours them. Everything visual lives
here so a colour is defined once.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

BG = "#14181d"
PANEL = "#1b2027"
ACCENT = "#4aa8ff"
TEXT = "#dfe6ee"
MUTED = "#8a97a6"
OK = "#4ade80"
WARN = "#fbbf24"
ERROR = "#f87171"
DOT_IDLE = "#6b7785"

FONT_UI = ("Segoe UI", 10)
FONT_HEADER = ("Segoe UI", 12, "bold")
FONT_MONO = ("Consolas", 9)


def enable_dpi_awareness() -> None:
    """Ask Windows not to bitmap-scale us; without this, text is blurry."""
    if sys.platform != "win32":
        return
    import ctypes

    with contextlib.suppress(Exception):
        ctypes.windll.shcore.SetProcessDpiAwareness(1)


def icon_path() -> Path | None:
    """The .ico, whether running from source or unpacked by PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    candidate = base / "assets" / "bridge.ico"
    return candidate if candidate.exists() else None


def apply_theme(root) -> None:
    from tkinter import ttk

    root.configure(bg=BG)
    style = ttk.Style(root)
    # "clam" is the one bundled theme that respects background/foreground on
    # Windows; the native theme silently ignores them.
    with contextlib.suppress(Exception):
        style.theme_use("clam")

    style.configure(".", background=BG, foreground=TEXT, font=FONT_UI,
                    fieldbackground=PANEL, borderwidth=0)
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=TEXT, font=FONT_UI)
    style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=FONT_UI)
    style.configure("Header.TLabel", background=BG, foreground=TEXT, font=FONT_HEADER)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONT_UI)
    style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED, font=FONT_UI)
    style.configure("Error.TLabel", background=BG, foreground=ERROR, font=FONT_UI)
    style.configure("Mono.TLabel", background=PANEL, foreground=TEXT, font=FONT_MONO)

    style.configure("TButton", background=PANEL, foreground=TEXT, font=FONT_UI,
                    padding=(10, 4), relief="flat")
    style.map("TButton",
              background=[("active", "#252c35"), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])
    style.configure("Accent.TButton", background=ACCENT, foreground="#0b0e12")
    style.map("Accent.TButton", background=[("active", "#6cbcff")])

    style.configure("TCheckbutton", background=BG, foreground=TEXT, font=FONT_UI)
    style.map("TCheckbutton", background=[("active", BG)])
    style.configure("TEntry", fieldbackground=PANEL, foreground=TEXT,
                    insertcolor=TEXT, padding=4)
    style.configure("TSpinbox", fieldbackground=PANEL, foreground=TEXT,
                    arrowcolor=TEXT, padding=4)
    style.configure("TCombobox", fieldbackground=PANEL, background=PANEL,
                    foreground=TEXT, arrowcolor=TEXT, padding=4)
    style.configure("TSeparator", background="#2a323c")
    style.configure("Vertical.TScrollbar", background=PANEL, troughcolor=BG,
                    arrowcolor=MUTED, borderwidth=0)

    with contextlib.suppress(Exception):
        icon = icon_path()
        if icon is not None:
            root.iconbitmap(default=str(icon))


def qr_rows(data: str, scale: int = 4) -> list[str]:
    """The QR as rows of hex pixels, ready for tk.PhotoImage.put.

    Rendering the matrix ourselves is what keeps Pillow out of the dependency
    list: `qrcode` gives us a boolean matrix, and PhotoImage.put takes exactly
    this shape of argument.
    """
    import qrcode

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=2)
    qr.add_data(data)
    qr.make(fit=True)

    rows = []
    for line in qr.get_matrix():
        pixels = []
        for module in line:
            pixels.extend(["#000000" if module else "#ffffff"] * scale)
        row = " ".join(pixels)
        rows.extend([row] * scale)
    return rows
