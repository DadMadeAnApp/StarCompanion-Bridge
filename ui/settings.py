"""The settings panel.

Validation happens before Save, not after a restart: a port that is already
taken would otherwise leave the bridge restarted into a dead state, with the
panel already closed and nothing on screen explaining it.
"""

from __future__ import annotations

import contextlib
import socket
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from core import autostart
from core.config import Config
from ui.theme import ERROR, apply_theme
from ui.widgets import Tooltip


def validate_port(value: str, current_port: int) -> str | None:
    """None if `value` is a usable port, otherwise the message to show."""
    text = value.strip()
    if not text.isdigit():
        return "Port must be a number."
    port = int(text)
    if not (1024 <= port <= 65535):
        return "Port must be between 1024 and 65535."
    if port == current_port:
        # Already ours. Test-binding it would report our own listener.
        return None
    probe = socket.socket()
    try:
        probe.bind(("0.0.0.0", port))
    except OSError:
        return f"Port {port} is already in use by something else."
    finally:
        probe.close()
    return None


def validate_log_path(value: str) -> str | None:
    """None if empty (auto-detect) or a readable file, otherwise the message."""
    text = value.strip()
    if not text:
        return None
    path = Path(text)
    if not path.exists():
        return "That path does not exist."
    if path.is_dir():
        return "Pick the Game.log file itself, not the folder."
    try:
        path.open("rb").close()
    except OSError:
        return "That file cannot be read."
    return None


class SettingsDialog:
    def __init__(self, parent, cfg: Config, app_dir: Path, on_save) -> None:
        self.cfg = cfg
        self.app_dir = Path(app_dir)
        self._on_save = on_save
        self.result: Config | None = None

        self.top = tk.Toplevel(parent)
        self.top.title("Settings")
        self.top.transient(parent)
        self.top.resizable(False, False)
        apply_theme(self.top)

        body = ttk.Frame(self.top, style="TFrame", padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Port", style="TLabel").grid(row=0, column=0, sticky="w")
        self.port_var = tk.StringVar(value=str(cfg.port))
        self.port_spin = ttk.Spinbox(body, from_=1024, to=65535, width=10,
                                     textvariable=self.port_var)
        self.port_spin.grid(row=0, column=1, sticky="w", padx=8)
        self.port_note = ttk.Label(body, text="", style="Muted.TLabel")
        self.port_note.grid(row=1, column=1, sticky="w", padx=8)
        if cfg.port_from_cli:
            self.port_spin.configure(state="disabled")
            self.port_note.configure(text="set by --port on the command line")

        ttk.Label(body, text="Game.log", style="TLabel").grid(row=2, column=0, sticky="w",
                                                              pady=(12, 0))
        self.log_var = tk.StringVar(value=cfg.log_path or "")
        self.log_entry = ttk.Entry(body, textvariable=self.log_var, width=38)
        self.log_entry.grid(row=2, column=1, sticky="ew", padx=8, pady=(12, 0))
        buttons = ttk.Frame(body, style="TFrame")
        buttons.grid(row=3, column=1, sticky="w", padx=8)
        self.browse_button = ttk.Button(buttons, text="Browse…", command=self._browse)
        self.browse_button.grid(row=0, column=0)
        self.auto_button = ttk.Button(buttons, text="Auto-detect", command=self._auto_detect)
        self.auto_button.grid(row=0, column=1, padx=6)
        self.log_note = ttk.Label(body, text=self._auto_note(), style="Muted.TLabel",
                                  wraplength=320, justify="left")
        self.log_note.grid(row=4, column=1, sticky="w", padx=8)
        if cfg.log_from_cli:
            for widget in (self.log_entry, self.browse_button, self.auto_button):
                widget.configure(state="disabled")
            self.log_note.configure(text="set by --log on the command line")

        self.telemetry_var = tk.BooleanVar(value=cfg.telemetry_enabled)
        ttk.Checkbutton(body, text="Live telemetry (mission, notifications, location)",
                        variable=self.telemetry_var).grid(row=5, column=0, columnspan=2,
                                                          sticky="w", pady=(14, 0))

        self.autostart_var = tk.BooleanVar(value=cfg.start_with_windows)
        self.autostart_check = ttk.Checkbutton(body, text="Start with Windows",
                                               variable=self.autostart_var)
        self.autostart_check.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))
        if not autostart.supported():
            self.autostart_check.configure(state="disabled")
            self.autostart_var.set(False)
            Tooltip(self.autostart_check, autostart.reason_unsupported())

        self.error_label = ttk.Label(body, text="", style="Error.TLabel",
                                     wraplength=380, justify="left")
        self.error_label.grid(row=7, column=0, columnspan=2, sticky="w", pady=(12, 0))

        actions = ttk.Frame(body, style="TFrame")
        actions.grid(row=8, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(actions, text="Cancel", command=self.cancel).grid(row=0, column=0, padx=6)
        self.save_button = ttk.Button(actions, text=self._save_label(),
                                      style="Accent.TButton", command=self.save)
        self.save_button.grid(row=0, column=1)
        self.port_var.trace_add("write", lambda *_: self.save_button.configure(
            text=self._save_label()))

    def _save_label(self) -> str:
        # Renaming the button is the honest way to tell the user their phone is
        # about to be disconnected for a moment.
        return "Save & Restart" if self.port_var.get().strip() != str(self.cfg.port) else "Save"

    def _auto_note(self) -> str:
        if self.log_var.get().strip():
            return ""
        from telemetry.tailer import default_log_path

        found = default_log_path()
        return f"Auto-detected: {found}" if found else "Auto-detect found no Game.log."

    def _browse(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.top, title="Select Game.log",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
        )
        if chosen:
            self.log_var.set(chosen)
            self.log_note.configure(text="")

    def _auto_detect(self) -> None:
        self.log_var.set("")
        self.log_note.configure(text=self._auto_note())

    def save(self) -> bool:
        message = None
        if not self.cfg.port_from_cli:
            message = validate_port(self.port_var.get(), current_port=self.cfg.port)
        if message is None and not self.cfg.log_from_cli:
            message = validate_log_path(self.log_var.get())
        if message is not None:
            self.error_label.configure(text=message, foreground=ERROR)
            return False

        updated = Config(
            port=int(self.port_var.get()) if not self.cfg.port_from_cli else self.cfg.port,
            log_path=(self.log_var.get().strip() or None) if not self.cfg.log_from_cli
            else self.cfg.log_path,
            telemetry_enabled=bool(self.telemetry_var.get()),
            start_with_windows=bool(self.autostart_var.get()),
            minimize_to_tray=self.cfg.minimize_to_tray,
            window_height=self.cfg.window_height,
            extras=dict(self.cfg.extras),
            port_from_cli=self.cfg.port_from_cli,
            log_from_cli=self.cfg.log_from_cli,
        )

        if autostart.supported():
            exe = Path(sys.executable)
            if updated.start_with_windows:
                autostart.enable(exe)
            else:
                autostart.disable()

        self.result = updated
        self._on_save(updated)
        with contextlib.suppress(Exception):
            self.top.destroy()
        return True

    def cancel(self) -> None:
        with contextlib.suppress(Exception):
            self.top.destroy()
