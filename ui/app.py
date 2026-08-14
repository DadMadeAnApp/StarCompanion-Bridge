"""The window: layout, the drain loop, and the server thread's lifecycle.

Tk owns the main thread because Windows requires Tk calls to come from the
thread that created the widgets. The server therefore runs in a daemon worker
whose only outward channel is EventBus.emit -- nothing in ServerThread may
touch a widget.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import traceback
from pathlib import Path

import bridge_server

# Imported lazily, matching ui/widgets.py: this machine (and some CI runners)
# has no working _tkinter, and ServerThread must stay importable regardless --
# it never touches a widget.
try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = None
    ttk = None
from core.config import Config, save
from core.events import (
    FATAL_CRASH,
    ClientCount,
    EventBus,
    Fatal,
    LogLine,
    Ready,
    TelemetryState,
)
from core.status import PORT_IN_USE, StatusModel
from ui.log_handler import configure_logging
from ui.theme import (
    ERROR,
    MUTED,
    OK,
    WARN,
    apply_theme,
    enable_dpi_awareness,
    qr_rows,
)
from ui.widgets import ActivityPane, CopyRow, StatusDot

DRAIN_INTERVAL_MS = 100
DRAIN_LIMIT = 200
WINDOW_WIDTH = 520
MIN_HEIGHT = 620


class ServerThread(threading.Thread):
    """Runs `bridge_server.serve` in its own asyncio loop."""

    def __init__(self, port: int, bus: EventBus) -> None:
        # Daemon: a tailer wedged on a locked file must not keep the process
        # alive after the window is gone.
        super().__init__(daemon=True, name="bridge-server")
        self._port = port
        self._bus = bus
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._ready_to_stop = threading.Event()

    @property
    def alive(self) -> bool:
        return self.is_alive()

    def run(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception as exc:  # noqa: BLE001 - the whole point is to report it
            kind = bridge_server.fatal_kind_for(exc)
            logging.error(f"Bridge stopped: {exc}")
            logging.error(traceback.format_exc())
            self._bus.emit(Fatal(kind=kind, message=str(exc)))

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._ready_to_stop.set()
        log_path = bridge_server._resolve_log_path()
        self._bus.emit(TelemetryState(
            found=log_path is not None,
            path=str(log_path) if log_path else None,
            feeds=3 if log_path is not None else 0,
        ))
        await bridge_server.serve(
            port=self._port,
            on_ready=self._on_ready,
            on_clients=lambda n: self._bus.emit(ClientCount(n)),
            stop=self._stop,
        )

    def _on_ready(self, info: dict) -> None:
        self._bus.emit(Ready(
            ips=tuple(info["ips"]),
            port=info["port"],
            token=info["token"],
            cert_fp=info["cert_fp"],
            pairing_uri=info["pairing_uri"],
        ))

    def stop(self, timeout: float = 3.0) -> bool:
        """Ask the loop to unwind and wait for it. True if the thread exited."""
        if self.ident is None:
            # Never started -- join() would raise RuntimeError on a thread
            # that was constructed but not start()ed. Not reachable via
            # BridgeApp today, but the invariant should hold on its own
            # rather than depending on every caller starting it first.
            return True
        self._ready_to_stop.wait(timeout=1.0)
        if self._loop is not None and self._stop is not None:
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._stop.set)
        self.join(timeout=timeout)
        return not self.is_alive()


class BridgeApp:
    def __init__(self, cfg: Config, bus: EventBus, app_dir: Path,
                 root: tk.Tk | None = None, autostart_server: bool = True) -> None:
        self.cfg = cfg
        self.bus = bus
        self.app_dir = Path(app_dir)
        self.status = StatusModel()
        self.server: ServerThread | None = None
        self._ips: list[str] = []
        self._token = ""
        self._cert_fp = ""
        self.qr_payload = ""

        self.root = root if root is not None else tk.Tk()
        self.root.title("StarCompanion Bridge")
        self.root.minsize(WINDOW_WIDTH, MIN_HEIGHT)
        self.root.geometry(f"{WINDOW_WIDTH}x{max(cfg.window_height, MIN_HEIGHT)}")
        # Width is fixed: the layout is a single column, and a stretched one just
        # leaves the pair card floating in space.
        self.root.resizable(False, True)
        apply_theme(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build()
        self._refresh_status()
        if autostart_server:
            self.start_server()
        self.root.after(DRAIN_INTERVAL_MS, self._tick)

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build(self) -> None:
        outer = ttk.Frame(self.root, style="TFrame", padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)

        header = ttk.Frame(outer, style="TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        self.dot = StatusDot(header)
        self.dot.grid(row=0, column=0, padx=(0, 8))
        self.header_label = ttk.Label(header, text="", style="Header.TLabel")
        self.header_label.grid(row=0, column=1, sticky="w")
        ttk.Button(header, text="Settings", command=self.open_settings).grid(row=0, column=2, padx=4)
        ttk.Button(header, text="Restart", command=self.restart).grid(row=0, column=3)

        # Failure row: hidden until a Fatal arrives, then it explains why and
        # offers the fix. This is what a console window cannot do -- today the
        # exe would simply vanish.
        self.failure = ttk.Frame(outer, style="TFrame", padding=(0, 8, 0, 0))
        self.failure_label = ttk.Label(self.failure, text="", style="Error.TLabel",
                                       wraplength=WINDOW_WIDTH - 60, justify="left")
        self.failure_label.grid(row=0, column=0, sticky="w", columnspan=2)
        self.retry_button = ttk.Button(self.failure, text="Retry",
                                       style="Accent.TButton", command=self.retry)
        self.retry_button.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.settings_shortcut = ttk.Button(self.failure, text="Open Settings",
                                           command=self.open_settings)
        self.settings_shortcut.grid(row=1, column=1, sticky="w", padx=6, pady=(6, 0))
        self.failure_visible = False
        self.settings_shortcut_visible = False

        card = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        card.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        card.columnconfigure(1, weight=1)

        self.qr_label = tk.Label(card, background="#ffffff", borderwidth=0)
        self.qr_label.grid(row=0, column=0, rowspan=4, padx=(0, 12), sticky="n")
        self._qr_image: tk.PhotoImage | None = None

        rows = ttk.Frame(card, style="Panel.TFrame")
        rows.grid(row=0, column=1, rowspan=4, sticky="new")
        rows.columnconfigure(0, weight=1)
        self.ip_row = CopyRow(rows, "IP")
        self.ip_row.grid(row=0, column=0, sticky="ew", pady=2)
        self.ip_selector = ttk.Combobox(rows, state="readonly", values=[])
        self.ip_selector.bind("<<ComboboxSelected>>",
                              lambda _e: self.select_ip(self.ip_selector.get()))
        self.port_row = CopyRow(rows, "Port")
        self.port_row.grid(row=2, column=0, sticky="ew", pady=2)
        self.token_row = CopyRow(rows, "Token", masked=True)
        self.token_row.grid(row=3, column=0, sticky="ew", pady=2)
        # masked: the 95-char colon-hex SHA-256 clips silently in this ~330px
        # column with no sign it's truncated. First4/last4 is all a human eye
        # comparison achieves anyway; Copy still yields the full string.
        self.cert_row = CopyRow(rows, "Cert", masked=True)
        self.cert_row.grid(row=4, column=0, sticky="ew", pady=2)

        self.qr_hint = ttk.Label(card, text="Scan in the StarCompanion app to pair",
                                 style="PanelMuted.TLabel")
        self.qr_hint.grid(row=4, column=0, columnspan=2, pady=(10, 0))
        self.qr_toggle = ttk.Button(card, text="Hide QR", width=10, command=self.toggle_qr)
        self.qr_toggle.grid(row=5, column=0, columnspan=2, pady=(6, 0))
        self._qr_hidden = False

        telemetry = ttk.Frame(outer, style="TFrame", padding=(0, 12, 0, 0))
        telemetry.grid(row=3, column=0, sticky="ew")
        ttk.Label(telemetry, text="Telemetry", style="Muted.TLabel").grid(row=0, column=0)
        self.telemetry_dot = StatusDot(telemetry, diameter=10)
        self.telemetry_dot.grid(row=0, column=1, padx=8)
        self.telemetry_label = ttk.Label(telemetry, text="starting…", style="TLabel")
        self.telemetry_label.grid(row=0, column=2, sticky="w")

        activity_header = ttk.Frame(outer, style="TFrame", padding=(0, 12, 0, 4))
        activity_header.grid(row=4, column=0, sticky="ew")
        activity_header.columnconfigure(1, weight=1)
        ttk.Label(activity_header, text="Activity", style="Muted.TLabel").grid(row=0, column=0)
        self._pause_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            activity_header, text="Pause", variable=self._pause_var,
            command=lambda: self.activity.set_paused(self._pause_var.get()),
        ).grid(row=0, column=2, padx=4)
        ttk.Button(activity_header, text="Clear", width=7,
                   command=lambda: self.activity.clear()).grid(row=0, column=3)

        self.activity = ActivityPane(outer)
        self.activity.grid(row=5, column=0, sticky="nsew")
        outer.rowconfigure(5, weight=1)

    # ── Event drain ───────────────────────────────────────────────────────────
    def _tick(self) -> None:
        # The reschedule must happen no matter what drain_once() does: Tk's
        # callback wrapper swallows an exception raised here, and if the
        # reschedule sat after that call in a plain sequence, one bad event
        # would silently stop the 100ms poll forever -- the window keeps
        # looking alive while nothing on it ever updates again.
        try:
            self.drain_once()
        finally:
            self.root.after(DRAIN_INTERVAL_MS, self._tick)

    def drain_once(self) -> None:
        events, dropped = self.bus.drain(limit=DRAIN_LIMIT)
        for event in events:
            # One malformed event must not take the rest of the batch down
            # with it -- report it in the activity pane and keep draining.
            try:
                self._handle(event)
            except Exception as exc:  # noqa: BLE001 - isolate, don't propagate
                self._report_handler_failure(event, exc)
        if dropped:
            plural = "event" if dropped == 1 else "events"
            self.activity.note(f"… {dropped} {plural} dropped")

    def _report_handler_failure(self, event, exc: Exception) -> None:
        """Log/note that an event's handling raised, without ever repeating
        the event's own contents.

        The event's repr is off-limits outright: Ready is a frozen dataclass
        whose generated repr includes the raw pairing token, and its
        pairing_uri field embeds the token again. The exception's *message*
        needs its own check, because Ready is also the one event type whose
        handling touches attacker- and format-sensitive data on the way to
        the screen (the QR encoder in particular) -- a ValueError from a
        string/encoding failure can echo the offending value back in
        str(exc), the same way `int("xyz")` echoes "xyz". So for Ready
        specifically, withhold the exception text too and log only the type;
        every other event type carries nothing sensitive and keeps the
        message for diagnosis.
        """
        kind = type(event).__name__
        # SECURITY: withhold the exception text for Ready specifically -- it's
        # the one event type whose handling touches attacker- and format-
        # sensitive data (the pairing token, the QR encoder) on the way to the
        # screen. A future event type carrying its own secret needs the same
        # treatment; don't assume this branch is Ready-specific trivia.
        if isinstance(event, Ready):
            detail = "message withheld -- Ready may carry the pairing token"
        else:
            detail = str(exc)
        logging.error(f"Failed to handle a {kind} event: {detail}")
        with contextlib.suppress(Exception):
            self.activity.note(f"… failed to handle a {kind} event: {detail}")

    def _handle(self, event) -> None:
        self.status.apply(event)
        if isinstance(event, Ready):
            self._ips = list(event.ips)
            self._token = event.token
            self._cert_fp = event.cert_fp
            self.qr_payload = event.pairing_uri
            self.port_row.set_value(str(event.port))
            self.token_row.set_value(event.token)
            self.cert_row.set_value(event.cert_fp)
            self._show_ips()
            self._draw_qr()
        elif isinstance(event, LogLine):
            self.activity.append(event.ts, event.level, event.text)
        elif isinstance(event, TelemetryState):
            self._show_telemetry(event)
        elif isinstance(event, Fatal):
            self._show_failure(event)
        self._refresh_status()

    def _refresh_status(self) -> None:
        self.header_label.configure(text=self.status.header_text)
        self.dot.set(self.status.color)

    # ── Pair card ─────────────────────────────────────────────────────────────
    @property
    def ip_choices(self) -> list[str]:
        return list(self._ips)

    def _show_ips(self) -> None:
        self.ip_row.set_value(self._ips[0] if self._ips else "")
        if len(self._ips) > 1:
            # Multiple adapters (VPN, Hyper-V, WSL) are common, and picking the
            # wrong one is the classic pairing failure. Let the user try each,
            # with the QR following the choice.
            self.ip_selector.configure(values=self._ips)
            self.ip_selector.set(self._ips[0])
            self.ip_selector.grid(row=1, column=0, sticky="ew", pady=2)
        else:
            self.ip_selector.grid_remove()

    def select_ip(self, ip: str) -> None:
        if ip not in self._ips:
            return
        self._ips = [ip] + [other for other in self._ips if other != ip]
        self.ip_row.set_value(ip)
        self.qr_payload = bridge_server._pairing_uri(
            self._ips, int(self.port_row.value or self.cfg.port), self._token,
            self._fingerprint_b64(),
        )
        self._draw_qr()

    def _fingerprint_b64(self) -> str:
        import base64

        raw = bytes.fromhex(self._cert_fp.replace(":", "")) if self._cert_fp else b""
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def _draw_qr(self) -> None:
        if not self.qr_payload or self._qr_hidden:
            return
        rows = qr_rows(self.qr_payload, scale=3)
        image = tk.PhotoImage(master=self.root,
                              width=len(rows[0].split()), height=len(rows))
        image.put(" ".join("{" + row + "}" for row in rows), to=(0, 0))
        self._qr_image = image  # a PhotoImage is garbage collected if unreferenced
        self.qr_label.configure(image=image)

    def toggle_qr(self) -> None:
        self._qr_hidden = not self._qr_hidden
        self.qr_toggle.configure(text="Show QR" if self._qr_hidden else "Hide QR")
        if self._qr_hidden:
            self.qr_label.configure(image="")
            self._qr_image = None
        else:
            self._draw_qr()

    def _show_telemetry(self, event: TelemetryState) -> None:
        if not self.cfg.telemetry_enabled:
            self.telemetry_dot.set(MUTED)
            self.telemetry_label.configure(text="turned off in Settings")
        elif event.found:
            self.telemetry_dot.set(OK)
            self.telemetry_label.configure(text=f"Game.log found — {event.feeds} feeds active")
        else:
            self.telemetry_dot.set(WARN)
            self.telemetry_label.configure(
                text="Game.log not found — set the path in Settings")

    # ── Failure row ───────────────────────────────────────────────────────────
    def _show_failure(self, event: Fatal) -> None:
        self.failure_label.configure(text=self.status.text if event.kind != FATAL_CRASH
                                     else f"Error — {event.message}")
        self.failure.grid(row=1, column=0, sticky="ew")
        self.failure_visible = True
        if self.status.status == PORT_IN_USE:
            self.settings_shortcut.grid()
            self.settings_shortcut_visible = True
        else:
            self.settings_shortcut.grid_remove()
            self.settings_shortcut_visible = False

    def _hide_failure(self) -> None:
        self.failure.grid_remove()
        self.failure_visible = False
        self.settings_shortcut_visible = False

    def show_startup_refusal(self, message: str) -> None:
        """A pre-window condition (a --log path that doesn't exist, a retired
        insecure-auth opt-out still present) that stopped the bridge before it
        ever tried to serve. No server was started, so there is nothing for
        Retry to retry -- the same argument would just fail the same way --
        so it's omitted rather than offered as a dead end.
        """
        self.header_label.configure(text="Startup blocked")
        self.dot.set(ERROR)
        self.failure_label.configure(text=message)
        self.failure.grid(row=1, column=0, sticky="ew")
        self.failure_visible = True
        self.retry_button.grid_remove()
        # A bad --log path or a retired flag is supplied on the command line,
        # which the settings panel can't override (those fields show as
        # CLI-controlled and disabled), so the shortcut would be a dead end
        # here too.
        self.settings_shortcut.grid_remove()
        self.settings_shortcut_visible = False

    def retry(self) -> None:
        # Don't assume the old thread is dead -- a Fatal from a crash means it
        # is, but nothing guarantees that in general, and starting a second
        # ServerThread while the first still holds the port just produces a
        # second, identical failure that Retry then loops on forever.
        if not self.stop_server():
            self._show_stop_failure()
            return
        self.status.reset()
        self._hide_failure()
        self._refresh_status()
        self.start_server()

    def _show_stop_failure(self) -> None:
        """The previous ServerThread didn't unwind in time (e.g. a tailer
        blocked on a file the game has locked). Starting a replacement here
        would leave two threads holding/fighting for the same port, so we
        don't -- just say so and let Retry try again."""
        self.failure_label.configure(
            text="The previous listener hasn't released the port yet — try Retry again in a moment."
        )
        self.failure.grid(row=1, column=0, sticky="ew")
        self.failure_visible = True
        self.retry_button.grid()
        self.settings_shortcut.grid_remove()
        self.settings_shortcut_visible = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start_server(self) -> None:
        bridge_server.set_telemetry_enabled(self.cfg.telemetry_enabled)
        bridge_server.set_log_path_override(Path(self.cfg.log_path) if self.cfg.log_path else None)
        self.server = ServerThread(port=self.cfg.port, bus=self.bus)
        self.server.start()

    def stop_server(self, timeout: float = 3.0) -> bool:
        if self.server is None:
            return True
        stopped = self.server.stop(timeout=timeout)
        if stopped:
            self.server = None
        # else: leave self.server pointing at the still-running thread. It
        # still holds the port and its handlers are still live; clearing the
        # reference here would make a caller believe it's safe to start a
        # replacement, which is exactly the two-servers-on-one-port bug this
        # guards against.
        return stopped

    def restart(self) -> None:
        if not self.stop_server():
            self._show_stop_failure()
            return
        self.status.reset()
        self._hide_failure()
        for row in (self.ip_row, self.port_row, self.token_row, self.cert_row):
            row.set_value("")
        self.ip_selector.grid_remove()
        self._ips = []
        self.qr_payload = ""
        # Otherwise the old QR sits on screen, pointing at credentials the
        # about-to-restart server no longer honours, until the next Ready.
        self.qr_label.configure(image="")
        self._qr_image = None
        self._refresh_status()
        self.start_server()

    def apply_settings(self, new_cfg: Config) -> bool:
        """Persist the panel's result and apply as much of it as we can live.

        Returns False if the config could not be saved to disk, so the
        settings panel can surface that instead of silently discarding the
        user's change and closing as if it worked.
        """
        port_changed = new_cfg.port != self.cfg.port
        log_changed = new_cfg.log_path != self.cfg.log_path
        # A telemetry toggle needs the same restart as a port change: each
        # connection builds its own LogTailer at accept time, so a currently
        # connected app keeps getting (or not getting) frames regardless of
        # this flag until the listener itself is replaced.
        telemetry_changed = new_cfg.telemetry_enabled != self.cfg.telemetry_enabled
        self.cfg = new_cfg
        saved = save(self.cfg, self.app_dir)
        bridge_server.set_telemetry_enabled(self.cfg.telemetry_enabled)
        if port_changed or log_changed or telemetry_changed:
            self.restart()
        return saved

    def open_settings(self) -> None:
        from ui.settings import SettingsDialog

        SettingsDialog(self.root, self.cfg, self.app_dir, on_save=self.apply_settings)

    def on_close(self) -> None:
        # Tk's WM_DELETE_WINDOW dispatcher swallows an exception raised here
        # to a stream that's None in a --windowed build, which would silently
        # skip the destroy() below and leave the window (and the daemon
        # server thread still holding the port) stuck open with no way to
        # close it but Task Manager. Nothing between here and destroy() may
        # be allowed to prevent that call from running.
        try:
            self.cfg.window_height = max(self.root.winfo_height(), MIN_HEIGHT)
            if not save(self.cfg, self.app_dir):
                logging.warning(f"Could not save {self.app_dir} settings on close.")
        except Exception:  # noqa: BLE001 - destroy() below must still run
            logging.exception("Error while saving settings on close.")
        try:
            if not self.stop_server(timeout=3.0):
                logging.warning("Server thread did not stop in time — exiting anyway.")
        except Exception:  # noqa: BLE001 - destroy() below must still run
            logging.exception("Error while stopping the server on close.")
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_gui(
    cfg: Config | None,
    app_dir: Path,
    config_warning: str | None = None,
    startup_warning: str | None = None,
    startup_refusal: str | None = None,
) -> None:
    """Build and run the window.

    `cfg` may be None when `startup_refusal` is set: a refusal (a bad --log
    path, a retired insecure-auth flag) is caught before any config is loaded,
    and the window still needs to open to show why, just without a server.

    `enable_dpi_awareness` runs here, before BridgeApp creates its `tk.Tk()` --
    see its docstring for why that ordering is required.
    """
    enable_dpi_awareness()
    bus = EventBus()
    configure_logging(bus, log_file=Path(app_dir) / "bridge.log")
    if config_warning:
        logging.warning(config_warning)
    if startup_warning:
        logging.warning(startup_warning)
    app = BridgeApp(cfg or Config(), bus, app_dir, autostart_server=startup_refusal is None)
    if startup_refusal:
        app.show_startup_refusal(startup_refusal)
    app.run()
