# Desktop GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bridge's console window with a dark-themed Tkinter window showing status, pairing details, live activity, and a settings panel, keeping console mode behind `--console`.

**Architecture:** The asyncio websocket server runs on a daemon worker thread; Tk owns the main thread. The two communicate through a bounded thread-safe `EventBus` (server to UI) and `loop.call_soon_threadsafe` (UI to server). The server thread never touches Tk. Existing server code is refactored only to separate binding from printing.

**Tech Stack:** Python 3.10+, Tkinter/ttk (stdlib), asyncio, websockets, pynput, cryptography, qrcode, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-14-bridge-gui-design.md`

## Global Constraints

- **No new dependencies.** `_DEPS` in `bridge_server.py:18` and the pip line in `build_exe.bat:15` must not gain entries. The QR is rendered from the `qrcode` package already present, via `qr.get_matrix()` into a `tk.PhotoImage`. No Pillow, no pystray.
- **Python 3.10 floor.** `X | None` unions are fine; `match` statements are fine. Nothing 3.11+.
- **The server thread never calls Tk.** Its only outward channel is `EventBus.emit`.
- **The existing four test files must pass untouched** — `tests/test_parsers.py`, `tests/test_tailer.py`, `tests/test_telemetry_wire.py`, `tests/test_bridge_server.py`. They are the guard that server behavior did not change. Never edit them to accommodate a refactor.
- **All new tests run headless on macOS.** No test may require a display, a Windows registry, or a real Star Citizen install. `winreg` is always mocked.
- **`--console` behavior is byte-identical to today's**: same banner text, same terminal QR, same stdout logging.
- **Auth is never optional.** No task adds a way to run without a pairing token.
- **Palette, verbatim:** background `#14181d`, panel `#1b2027`, accent `#4aa8ff`, text `#dfe6ee`, muted `#8a97a6`, ok `#4ade80`, warn `#fbbf24`, error `#f87171`, grey dot `#6b7785`.
- **Fonts, verbatim:** `("Segoe UI", 10)` for chrome, `("Segoe UI", 12, "bold")` for the header, `("Consolas", 9)` for the activity pane.
- **Window geometry:** width fixed at 520, minimum height 620, resizable vertically only.
- **Config file name:** `bridge_config.json`, in `_app_dir()`. Corrupt files are renamed `bridge_config.json.bad`, never fatal.
- **Two modules beyond the spec's file list:** `core/status.py` and `core/autostart.py`. The spec describes both behaviours (the status state table, the Run-key entry) but folded them into the UI layer. They live in `core/` instead because both are pure logic that must be tested without a display, and `winreg` has to be mockable.

---

### Task 1: Event types and the thread-safe bus

**Files:**
- Create: `core/__init__.py` (empty)
- Create: `core/events.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Ready(ips: tuple[str, ...], port: int, token: str, cert_fp: str, pairing_uri: str)`
  - `ClientCount(n: int)`
  - `TelemetryState(found: bool, path: str | None, feeds: int)`
  - `LogLine(ts: str, level: int, text: str)`
  - `Fatal(kind: str, message: str)` where `kind` is `"PORT_IN_USE"` or `"CRASH"`
  - `FATAL_PORT_IN_USE = "PORT_IN_USE"`, `FATAL_CRASH = "CRASH"`
  - `EventBus(maxsize: int = 2000)` with `emit(event) -> bool` and `drain(limit: int = 200) -> tuple[list, int]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_events.py`:

```python
import logging

from core.events import (
    FATAL_CRASH,
    FATAL_PORT_IN_USE,
    ClientCount,
    EventBus,
    Fatal,
    LogLine,
    Ready,
    TelemetryState,
)


def test_emit_then_drain_preserves_order():
    bus = EventBus()
    bus.emit(ClientCount(1))
    bus.emit(ClientCount(2))
    events, dropped = bus.drain()
    assert [e.n for e in events] == [1, 2]
    assert dropped == 0


def test_drain_is_bounded_and_leaves_the_rest():
    bus = EventBus()
    for i in range(10):
        bus.emit(ClientCount(i))
    first, _ = bus.drain(limit=4)
    assert [e.n for e in first] == [0, 1, 2, 3]
    rest, _ = bus.drain(limit=100)
    assert [e.n for e in rest] == [4, 5, 6, 7, 8, 9]


def test_drain_of_empty_bus_returns_nothing():
    bus = EventBus()
    assert bus.drain() == ([], 0)


def test_emit_never_blocks_when_full_and_reports_the_drop():
    bus = EventBus(maxsize=2)
    assert bus.emit(ClientCount(1)) is True
    assert bus.emit(ClientCount(2)) is True
    assert bus.emit(ClientCount(3)) is False  # dropped, did not block
    events, dropped = bus.drain()
    assert [e.n for e in events] == [1, 2]
    assert dropped == 1


def test_drop_count_resets_after_being_reported():
    bus = EventBus(maxsize=1)
    bus.emit(ClientCount(1))
    bus.emit(ClientCount(2))
    bus.emit(ClientCount(3))
    _, dropped = bus.drain()
    assert dropped == 2
    _, dropped_again = bus.drain()
    assert dropped_again == 0


def test_events_carry_their_documented_fields():
    ready = Ready(
        ips=("192.168.1.42",), port=8765, token="abc",
        cert_fp="A1:B2", pairing_uri="starcompanion://pair?x=1",
    )
    assert ready.ips == ("192.168.1.42",)
    assert ready.port == 8765
    assert TelemetryState(found=False, path=None, feeds=0).found is False
    assert LogLine(ts="21:04:11", level=logging.WARNING, text="hi").level == logging.WARNING
    assert Fatal(kind=FATAL_PORT_IN_USE, message="in use").kind == "PORT_IN_USE"
    assert FATAL_CRASH == "CRASH"


def test_events_are_immutable():
    import dataclasses
    import pytest

    event = ClientCount(1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.n = 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core'`

- [ ] **Step 3: Write the implementation**

Create `core/__init__.py` as an empty file. Create `core/events.py`:

```python
"""Events the server thread sends to the GUI thread, and the queue that carries them.

The server runs in a worker thread and must never touch Tk, so every piece of
state the window shows arrives as one of the frozen events below. The bus is
deliberately bounded: at 100 messages/second the server can outrun a 10Hz drain
loop, and a queue that grows without limit would turn a burst into unbounded
memory. Dropping and counting is better than either blocking the server or
losing lines silently.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass

FATAL_PORT_IN_USE = "PORT_IN_USE"
FATAL_CRASH = "CRASH"


@dataclass(frozen=True)
class Ready:
    """The server is bound and listening; everything needed to pair is known."""

    ips: tuple[str, ...]
    port: int
    token: str
    cert_fp: str
    pairing_uri: str


@dataclass(frozen=True)
class ClientCount:
    n: int


@dataclass(frozen=True)
class TelemetryState:
    found: bool
    path: str | None
    feeds: int


@dataclass(frozen=True)
class LogLine:
    ts: str
    level: int
    text: str


@dataclass(frozen=True)
class Fatal:
    """The server thread is dead. `kind` is FATAL_PORT_IN_USE or FATAL_CRASH."""

    kind: str
    message: str


class EventBus:
    """A bounded, thread-safe, never-blocking channel from server to GUI."""

    def __init__(self, maxsize: int = 2000) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=maxsize)
        self._dropped = 0
        # emit() runs on the server thread and drain() on the GUI thread, so the
        # drop counter needs its own lock -- Queue's internal one doesn't cover it.
        self._lock = __import__("threading").Lock()

    def emit(self, event) -> bool:
        """Queue an event. Returns False if the bus was full and it was dropped."""
        try:
            self._q.put_nowait(event)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return False
        return True

    def drain(self, limit: int = 200) -> tuple[list, int]:
        """Pull up to `limit` events, plus the number dropped since the last drain."""
        events = []
        for _ in range(limit):
            try:
                events.append(self._q.get_nowait())
            except queue.Empty:
                break
        with self._lock:
            dropped, self._dropped = self._dropped, 0
        return events, dropped
```

Replace the `__import__("threading")` line with a normal `import threading` at the top of the file and `threading.Lock()` here — it is written inline above only to keep the snippet self-contained.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_events.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add core/__init__.py core/events.py tests/test_events.py
git commit -m "feat(ui): add the event types and bounded bus the GUI reads from"
```

---

### Task 2: Config file

**Files:**
- Create: `core/config.py`
- Modify: `.gitignore`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `CONFIG_NAME = "bridge_config.json"`, `CONFIG_VERSION = 1`
  - `Config` dataclass (mutable): `port: int = 8765`, `log_path: str | None = None`, `telemetry_enabled: bool = True`, `start_with_windows: bool = False`, `minimize_to_tray: bool = False`, `window_height: int = 620`, `extras: dict = {}`, `port_from_cli: bool = False`, `log_from_cli: bool = False`
  - `load(app_dir: Path) -> tuple[Config, str | None]` — the string is a warning to log, or None
  - `save(cfg: Config, app_dir: Path) -> None`
  - `apply_cli(cfg: Config, cli_port: int | None, cli_log: str | None) -> Config`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
import json

from core.config import CONFIG_NAME, CONFIG_VERSION, Config, apply_cli, load, save


def test_load_with_no_file_returns_defaults_and_no_warning(tmp_path):
    cfg, warning = load(tmp_path)
    assert cfg.port == 8765
    assert cfg.log_path is None
    assert cfg.telemetry_enabled is True
    assert cfg.start_with_windows is False
    assert cfg.window_height == 620
    assert warning is None


def test_save_then_load_round_trips(tmp_path):
    cfg = Config(port=9000, log_path="F:/SC/Game.log", telemetry_enabled=False,
                 start_with_windows=True, window_height=800)
    save(cfg, tmp_path)
    loaded, warning = load(tmp_path)
    assert warning is None
    assert loaded.port == 9000
    assert loaded.log_path == "F:/SC/Game.log"
    assert loaded.telemetry_enabled is False
    assert loaded.start_with_windows is True
    assert loaded.window_height == 800


def test_saved_file_records_its_version(tmp_path):
    save(Config(), tmp_path)
    written = json.loads((tmp_path / CONFIG_NAME).read_text())
    assert written["version"] == CONFIG_VERSION
    assert written["window"] == {"height": 620}


def test_unknown_keys_survive_a_save(tmp_path):
    (tmp_path / CONFIG_NAME).write_text(json.dumps(
        {"version": 1, "port": 8765, "future_setting": "keep me"}
    ))
    cfg, _ = load(tmp_path)
    assert cfg.extras["future_setting"] == "keep me"
    save(cfg, tmp_path)
    written = json.loads((tmp_path / CONFIG_NAME).read_text())
    assert written["future_setting"] == "keep me"
    assert written["port"] == 8765


def test_corrupt_file_is_quarantined_and_defaults_are_used(tmp_path):
    (tmp_path / CONFIG_NAME).write_text("{not json at all")
    cfg, warning = load(tmp_path)
    assert cfg.port == 8765
    assert warning is not None and "bridge_config.json.bad" in warning
    assert (tmp_path / "bridge_config.json.bad").exists()
    assert not (tmp_path / CONFIG_NAME).exists()


def test_a_json_file_that_is_not_an_object_is_also_quarantined(tmp_path):
    (tmp_path / CONFIG_NAME).write_text("[1, 2, 3]")
    cfg, warning = load(tmp_path)
    assert cfg.port == 8765
    assert warning is not None


def test_wrong_typed_values_fall_back_per_field(tmp_path):
    (tmp_path / CONFIG_NAME).write_text(json.dumps(
        {"version": 1, "port": "not a number", "telemetry_enabled": "yes"}
    ))
    cfg, warning = load(tmp_path)
    assert cfg.port == 8765
    assert cfg.telemetry_enabled is True
    assert warning is not None


def test_cli_flags_win_over_the_config_file(tmp_path):
    cfg = Config(port=9000, log_path="F:/from/config/Game.log")
    merged = apply_cli(cfg, cli_port=7000, cli_log="D:/from/cli/Game.log")
    assert merged.port == 7000
    assert merged.log_path == "D:/from/cli/Game.log"
    assert merged.port_from_cli is True
    assert merged.log_from_cli is True


def test_absent_cli_flags_leave_the_config_alone(tmp_path):
    cfg = Config(port=9000, log_path="F:/from/config/Game.log")
    merged = apply_cli(cfg, cli_port=None, cli_log=None)
    assert merged.port == 9000
    assert merged.log_path == "F:/from/config/Game.log"
    assert merged.port_from_cli is False
    assert merged.log_from_cli is False


def test_cli_provenance_is_never_written_to_disk(tmp_path):
    cfg = apply_cli(Config(), cli_port=7000, cli_log=None)
    save(cfg, tmp_path)
    written = json.loads((tmp_path / CONFIG_NAME).read_text())
    assert "port_from_cli" not in written
    assert "log_from_cli" not in written
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.config'`

- [ ] **Step 3: Write the implementation**

Create `core/config.py`:

```python
"""Persisted settings for the GUI.

Precedence is CLI flag > config file > default. `apply_cli` records which
fields a flag supplied so the settings panel can disable them rather than
appear to control something it cannot.

A config file is a convenience, never a prerequisite: anything unreadable or
unparseable is moved aside and the defaults are used, because refusing to
start over a bad settings file would be worse than ignoring it.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "bridge_config.json"
CONFIG_VERSION = 1

# Keys this version owns. Anything else in the file is preserved in `extras` so
# a config written by a newer build survives a round-trip through an older one.
_KNOWN = {
    "version", "port", "log_path", "telemetry_enabled",
    "start_with_windows", "minimize_to_tray", "window",
}


@dataclass
class Config:
    port: int = 8765
    log_path: str | None = None
    telemetry_enabled: bool = True
    start_with_windows: bool = False
    # Reserved for a future tray icon. Read and written, never surfaced in the
    # settings panel -- the tray is out of scope for this work.
    minimize_to_tray: bool = False
    window_height: int = 620
    extras: dict = field(default_factory=dict)
    # Provenance, not settings: never persisted.
    port_from_cli: bool = False
    log_from_cli: bool = False


def config_path(app_dir: Path) -> Path:
    return Path(app_dir) / CONFIG_NAME


def load(app_dir: Path) -> tuple[Config, str | None]:
    """Read the config. Returns (config, warning-to-log-or-None)."""
    path = config_path(app_dir)
    if not path.exists():
        return Config(), None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Config(), _quarantine(path)
    if not isinstance(raw, dict):
        return Config(), _quarantine(path)

    cfg = Config()
    bad_fields = []

    def take(key: str, kind: type, target: str) -> None:
        if key not in raw:
            return
        value = raw[key]
        # bool is a subclass of int, so an explicit check keeps `"port": true`
        # from being accepted as a port number.
        if isinstance(value, kind) and not (kind is int and isinstance(value, bool)):
            setattr(cfg, target, value)
        else:
            bad_fields.append(key)

    take("port", int, "port")
    take("telemetry_enabled", bool, "telemetry_enabled")
    take("start_with_windows", bool, "start_with_windows")
    take("minimize_to_tray", bool, "minimize_to_tray")

    if "log_path" in raw:
        if raw["log_path"] is None or isinstance(raw["log_path"], str):
            cfg.log_path = raw["log_path"]
        else:
            bad_fields.append("log_path")

    window = raw.get("window")
    if isinstance(window, dict) and isinstance(window.get("height"), int):
        cfg.window_height = window["height"]
    elif window is not None:
        bad_fields.append("window")

    cfg.extras = {k: v for k, v in raw.items() if k not in _KNOWN}

    warning = None
    if bad_fields:
        warning = (
            f"{CONFIG_NAME}: ignoring unusable value(s) for "
            f"{', '.join(sorted(bad_fields))} — using defaults for those."
        )
    return cfg, warning


def _quarantine(path: Path) -> str:
    bad = path.with_suffix(path.suffix + ".bad")
    with contextlib.suppress(OSError):
        bad.unlink()
    with contextlib.suppress(OSError):
        path.rename(bad)
    return f"{CONFIG_NAME} was unreadable — moved to {bad.name} and using defaults."


def save(cfg: Config, app_dir: Path) -> None:
    """Write the config, preserving any keys this version does not know about."""
    payload = dict(cfg.extras)
    payload.update({
        "version": CONFIG_VERSION,
        "port": cfg.port,
        "log_path": cfg.log_path,
        "telemetry_enabled": cfg.telemetry_enabled,
        "start_with_windows": cfg.start_with_windows,
        "minimize_to_tray": cfg.minimize_to_tray,
        "window": {"height": cfg.window_height},
    })
    path = config_path(app_dir)
    # Write via a temp file so an interrupted save can't leave a truncated
    # config that the next launch has to quarantine.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def apply_cli(cfg: Config, cli_port: int | None, cli_log: str | None) -> Config:
    """Overlay command-line flags onto a loaded config, recording provenance."""
    if cli_port is not None:
        cfg.port = cli_port
        cfg.port_from_cli = True
    if cli_log is not None:
        cfg.log_path = cli_log
        cfg.log_from_cli = True
    return cfg
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: 10 passed

- [ ] **Step 5: Add the config file to .gitignore**

Append to `.gitignore`:

```
bridge_config.json
bridge_config.json.bad
bridge.log
```

- [ ] **Step 6: Commit**

```bash
git add core/config.py tests/test_config.py .gitignore
git commit -m "feat(ui): persist settings in bridge_config.json

Precedence is CLI flag > config file > default, with provenance recorded so
the settings panel can disable a field a flag already controls rather than
appear to own it. A corrupt file is moved aside rather than being fatal."
```

---

### Task 3: Status state machine

**Files:**
- Create: `core/status.py`
- Test: `tests/test_status.py`

**Interfaces:**
- Consumes: `core.events.Ready`, `ClientCount`, `Fatal`, `FATAL_PORT_IN_USE`, `FATAL_CRASH`.
- Produces:
  - `Status` constants: `STARTING`, `LISTENING`, `CONNECTED`, `PORT_IN_USE`, `ERROR` (plain module-level strings)
  - `StatusModel()` with `apply(event) -> None`, `reset() -> None`, and read-only properties `status: str`, `text: str`, `color: str`, `clients: int`, `fatal: bool`
  - `color` returns a palette hex string.

- [ ] **Step 1: Write the failing test**

Create `tests/test_status.py`:

```python
from core.events import FATAL_CRASH, FATAL_PORT_IN_USE, ClientCount, Fatal, Ready
from core.status import CONNECTED, ERROR, LISTENING, PORT_IN_USE, STARTING, StatusModel


def _ready(port=8765):
    return Ready(ips=("192.168.1.42",), port=port, token="abc",
                 cert_fp="A1:B2", pairing_uri="starcompanion://pair")


def test_starts_in_the_starting_state():
    model = StatusModel()
    assert model.status == STARTING
    assert model.text == "Starting…"
    assert model.color == "#6b7785"
    assert model.fatal is False


def test_ready_moves_to_listening():
    model = StatusModel()
    model.apply(_ready())
    assert model.status == LISTENING
    assert model.text == "Listening — waiting for app"
    assert model.color == "#4aa8ff"


def test_one_client_moves_to_connected_and_is_singular():
    model = StatusModel()
    model.apply(_ready())
    model.apply(ClientCount(1))
    assert model.status == CONNECTED
    assert model.text == "Connected — 1 app"
    assert model.color == "#4ade80"


def test_several_clients_pluralise():
    model = StatusModel()
    model.apply(_ready())
    model.apply(ClientCount(3))
    assert model.text == "Connected — 3 apps"


def test_last_client_leaving_returns_to_listening():
    model = StatusModel()
    model.apply(_ready())
    model.apply(ClientCount(1))
    model.apply(ClientCount(0))
    assert model.status == LISTENING
    assert model.text == "Listening — waiting for app"


def test_port_in_use_names_the_port_it_tried():
    model = StatusModel()
    model.apply(Fatal(kind=FATAL_PORT_IN_USE, message="port 8765 is already in use"))
    assert model.status == PORT_IN_USE
    assert model.text == "Port 8765 in use — is the bridge already running?"
    assert model.color == "#f87171"
    assert model.fatal is True


def test_port_in_use_falls_back_when_the_port_cannot_be_read():
    model = StatusModel()
    model.apply(Fatal(kind=FATAL_PORT_IN_USE, message="address in use"))
    assert model.text == "Port in use — is the bridge already running?"


def test_port_in_use_prefers_the_port_already_seen_from_ready():
    model = StatusModel()
    model.apply(_ready(port=9000))
    model.apply(Fatal(kind=FATAL_PORT_IN_USE, message="address in use"))
    assert model.text == "Port 9000 in use — is the bridge already running?"


def test_a_crash_reports_error():
    model = StatusModel()
    model.apply(Fatal(kind=FATAL_CRASH, message="boom"))
    assert model.status == ERROR
    assert model.text == "Error — see activity log"
    assert model.fatal is True


def test_fatal_latches_until_reset():
    model = StatusModel()
    model.apply(Fatal(kind=FATAL_CRASH, message="boom"))
    model.apply(_ready())
    model.apply(ClientCount(2))
    assert model.status == ERROR  # a dead thread's late events cannot clear it
    model.reset()
    assert model.status == STARTING
    assert model.fatal is False
    model.apply(_ready())
    assert model.status == LISTENING


def test_reset_clears_the_client_count():
    model = StatusModel()
    model.apply(_ready())
    model.apply(ClientCount(2))
    model.reset()
    assert model.clients == 0


def test_unrelated_events_are_ignored():
    from core.events import TelemetryState

    model = StatusModel()
    model.apply(_ready())
    model.apply(TelemetryState(found=True, path="F:/Game.log", feeds=2))
    assert model.status == LISTENING
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_status.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.status'`

- [ ] **Step 3: Write the implementation**

Create `core/status.py`:

```python
"""The one place that decides what the header dot and text say.

Keeping this out of the widget layer means every state transition is testable
without a display, and there is no second opinion about what colour "connected"
is.
"""

from __future__ import annotations

import re

from core.events import FATAL_PORT_IN_USE, ClientCount, Fatal, Ready

STARTING = "STARTING"
LISTENING = "LISTENING"
CONNECTED = "CONNECTED"
PORT_IN_USE = "PORT_IN_USE"
ERROR = "ERROR"

_GREY = "#6b7785"
_BLUE = "#4aa8ff"
_GREEN = "#4ade80"
_RED = "#f87171"

_COLORS = {
    STARTING: _GREY,
    LISTENING: _BLUE,
    CONNECTED: _GREEN,
    PORT_IN_USE: _RED,
    ERROR: _RED,
}

_PORT_IN_MESSAGE = re.compile(r"port (\d+)")


class StatusModel:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Back to STARTING, clearing any latched failure. Used by Retry/Restart."""
        self._status = STARTING
        self._clients = 0
        self._port: int | None = None

    def apply(self, event) -> None:
        # A fatal state latches. The server thread is dead by then, and an event
        # it queued just before dying must not paint the window as healthy.
        if self.fatal:
            return
        if isinstance(event, Ready):
            self._port = event.port
            self._status = LISTENING
        elif isinstance(event, ClientCount):
            self._clients = event.n
            self._status = CONNECTED if event.n > 0 else LISTENING
        elif isinstance(event, Fatal):
            if event.kind == FATAL_PORT_IN_USE:
                self._status = PORT_IN_USE
                if self._port is None:
                    found = _PORT_IN_MESSAGE.search(event.message)
                    self._port = int(found.group(1)) if found else None
            else:
                self._status = ERROR

    @property
    def status(self) -> str:
        return self._status

    @property
    def clients(self) -> int:
        return self._clients

    @property
    def fatal(self) -> bool:
        return self._status in (PORT_IN_USE, ERROR)

    @property
    def color(self) -> str:
        return _COLORS[self._status]

    @property
    def text(self) -> str:
        if self._status == STARTING:
            return "Starting…"
        if self._status == LISTENING:
            return "Listening — waiting for app"
        if self._status == CONNECTED:
            plural = "app" if self._clients == 1 else "apps"
            return f"Connected — {self._clients} {plural}"
        if self._status == PORT_IN_USE:
            where = f"Port {self._port}" if self._port else "Port"
            return f"{where} in use — is the bridge already running?"
        return "Error — see activity log"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_status.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add core/status.py tests/test_status.py
git commit -m "feat(ui): add the status state machine behind the header dot

A latching fatal state is the point: the server thread is dead by then, and an
event it queued just before dying must not repaint the window as healthy."
```

---

### Task 4: Logging into the activity pane

**Files:**
- Create: `ui/__init__.py` (empty)
- Create: `ui/log_handler.py`
- Test: `tests/test_log_handler.py`

**Interfaces:**
- Consumes: `core.events.EventBus`, `core.events.LogLine`.
- Produces:
  - `GuiLogHandler(bus: EventBus)` — a `logging.Handler`
  - `configure_logging(bus: EventBus | None, log_file: Path | None = None) -> None`
  - `LEVEL_COLORS: dict[int, str]` mapping `logging.INFO/WARNING/ERROR` to palette hex
  - `color_for_level(level: int) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_log_handler.py`:

```python
import logging

from core.events import EventBus, LogLine
from ui.log_handler import GuiLogHandler, color_for_level, configure_logging


def _record(level=logging.INFO, msg="hello"):
    return logging.LogRecord("test", level, __file__, 1, msg, None, None)


def test_a_record_becomes_a_logline_event():
    bus = EventBus()
    GuiLogHandler(bus).emit(_record(msg="App connected"))
    events, _ = bus.drain()
    assert len(events) == 1
    assert isinstance(events[0], LogLine)
    assert events[0].text == "App connected"
    assert events[0].level == logging.INFO


def test_the_timestamp_is_wall_clock_hh_mm_ss():
    import re

    bus = EventBus()
    GuiLogHandler(bus).emit(_record())
    events, _ = bus.drain()
    assert re.fullmatch(r"\d\d:\d\d:\d\d", events[0].ts)


def test_formatting_arguments_are_applied():
    bus = EventBus()
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "KEY %s -> %s",
                               ("quantum", "b"), None)
    GuiLogHandler(bus).emit(record)
    events, _ = bus.drain()
    assert events[0].text == "KEY quantum -> b"


def test_levels_map_to_the_palette():
    assert color_for_level(logging.INFO) == "#dfe6ee"
    assert color_for_level(logging.WARNING) == "#fbbf24"
    assert color_for_level(logging.ERROR) == "#f87171"
    assert color_for_level(logging.CRITICAL) == "#f87171"
    assert color_for_level(logging.DEBUG) == "#8a97a6"


def test_a_full_bus_does_not_raise_inside_logging():
    bus = EventBus(maxsize=1)
    handler = GuiLogHandler(bus)
    handler.emit(_record())
    handler.emit(_record())  # dropped, must not raise -- logging swallows nothing here
    events, dropped = bus.drain()
    assert len(events) == 1
    assert dropped == 1


def test_gui_mode_installs_no_stream_handler_so_a_windowed_exe_cannot_crash(monkeypatch, tmp_path):
    # PyInstaller --windowed leaves sys.stdout as None. A StreamHandler built on
    # that raises on the first record, which would kill the very first log line.
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        configure_logging(EventBus(), log_file=tmp_path / "bridge.log")
        assert not any(type(h) is logging.StreamHandler for h in root.handlers)
        logging.info("this must not raise")
    finally:
        root.handlers[:] = saved


def test_gui_mode_writes_to_the_log_file(tmp_path):
    root = logging.getLogger()
    saved = root.handlers[:]
    log_file = tmp_path / "bridge.log"
    try:
        configure_logging(EventBus(), log_file=log_file)
        logging.warning("written to disk")
        for handler in root.handlers:
            handler.flush()
    finally:
        for handler in root.handlers:
            if handler not in saved:
                handler.close()
        root.handlers[:] = saved
    assert "written to disk" in log_file.read_text(encoding="utf-8")


def test_console_mode_installs_a_stream_handler_and_no_gui_handler(tmp_path):
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        configure_logging(None)
        assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
        assert not any(isinstance(h, GuiLogHandler) for h in root.handlers)
    finally:
        root.handlers[:] = saved


def test_configure_logging_replaces_handlers_rather_than_stacking_them(tmp_path):
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        configure_logging(EventBus(), log_file=tmp_path / "bridge.log")
        first = len(root.handlers)
        configure_logging(EventBus(), log_file=tmp_path / "bridge.log")
        assert len(root.handlers) == first
    finally:
        for handler in root.handlers:
            if handler not in saved:
                handler.close()
        root.handlers[:] = saved
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_log_handler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui'`

- [ ] **Step 3: Write the implementation**

Create `ui/__init__.py` as an empty file. Create `ui/log_handler.py`:

```python
"""Routes the logging calls already scattered through the server into the window.

Every `logging.info` in bridge_server.py and telemetry/ becomes an activity-pane
line with no edit at the call site, which is the whole reason the activity pane
is a log handler and not a bespoke channel.
"""

from __future__ import annotations

import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.events import EventBus, LogLine

_TEXT = "#dfe6ee"
_MUTED = "#8a97a6"
_WARN = "#fbbf24"
_ERROR = "#f87171"

LEVEL_COLORS = {
    logging.DEBUG: _MUTED,
    logging.INFO: _TEXT,
    logging.WARNING: _WARN,
    logging.ERROR: _ERROR,
    logging.CRITICAL: _ERROR,
}


def color_for_level(level: int) -> str:
    """Nearest colour at or below `level`, so custom levels still map sanely."""
    for threshold in sorted(LEVEL_COLORS, reverse=True):
        if level >= threshold:
            return LEVEL_COLORS[threshold]
    return _MUTED


class GuiLogHandler(logging.Handler):
    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        # A dropped line is acceptable; an exception escaping a log call in the
        # middle of keystroke injection is not.
        try:
            self._bus.emit(LogLine(
                ts=time.strftime("%H:%M:%S", time.localtime(record.created)),
                level=record.levelno,
                text=record.getMessage(),
            ))
        except Exception:  # noqa: BLE001 - handleError is the logging contract
            self.handleError(record)


def configure_logging(bus: EventBus | None, log_file: Path | None = None) -> None:
    """Install handlers for one of the two modes.

    GUI mode (`bus` given) deliberately installs no StreamHandler: under a
    PyInstaller --windowed build sys.stdout is None, and a StreamHandler built
    on it raises on the first record emitted.
    """
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.INFO)

    if bus is None:
        # Console mode: today's behaviour, unchanged.
        stream = sys.stdout if sys.stdout is not None else sys.stderr
        if stream is not None:
            handler = logging.StreamHandler(stream)
            handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
            root.addHandler(handler)
        return

    root.addHandler(GuiLogHandler(bus))
    if log_file is not None:
        # 1MB x 2 is plenty to carry a crash traceback to a bug report without
        # letting a long session fill the user's disk.
        file_handler = RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=2, encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        root.addHandler(file_handler)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_log_handler.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add ui/__init__.py ui/log_handler.py tests/test_log_handler.py
git commit -m "feat(ui): route logging records to the activity pane and a log file

GUI mode installs no StreamHandler on purpose: a --windowed exe has no stdout,
and a StreamHandler built on None raises on the first record."
```

---

### Task 5: Separate binding from printing in the server

**Files:**
- Modify: `bridge_server.py:878-918` (`main`), `bridge_server.py:685-704` and `:837` (connection registry), `bridge_server.py:171-183` (logging setup)
- Test: `tests/test_serve_refactor.py`

**Interfaces:**
- Consumes: nothing from earlier tasks — `bridge_server.py` must not import `core` or `ui` at module scope, so console mode keeps working with those packages absent.
- Produces:
  - `async def serve(port: int = PORT, on_ready: Callable[[dict], None] | None = None, on_clients: Callable[[int], None] | None = None, stop: asyncio.Event | None = None) -> None`
  - `on_ready` receives a plain dict: `{"ips": list[str], "port": int, "token": str, "cert_fp": str, "pairing_uri": str}`
  - `def console_banner(info: dict) -> None` — every current `print` from `main`
  - `def fatal_kind_for(exc: BaseException) -> str` — returns `"PORT_IN_USE"` or `"CRASH"`
  - `async def main(port: int = PORT) -> None` — kept, now `serve` + `console_banner`
  - `def set_telemetry_enabled(enabled: bool) -> None` and module global `_telemetry_enabled`

- [ ] **Step 1: Write the failing test**

Create `tests/test_serve_refactor.py`:

```python
import asyncio
import errno
import logging

import pytest

import bridge_server


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_serve_reports_everything_needed_to_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    seen = {}
    stop = None

    async def run():
        nonlocal stop
        stop = asyncio.Event()

        def on_ready(info):
            seen.update(info)
            stop.set()

        await bridge_server.serve(port=_free_port(), on_ready=on_ready, stop=stop)

    asyncio.run(run())
    assert set(seen) == {"ips", "port", "token", "cert_fp", "pairing_uri"}
    assert seen["token"]
    assert seen["pairing_uri"].startswith("starcompanion://pair")
    assert isinstance(seen["ips"], list) and seen["ips"]


def test_serve_returns_when_stop_is_set(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(
            bridge_server.serve(port=_free_port(), on_ready=lambda info: None, stop=stop)
        )
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(run())


def test_serve_prints_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)

    async def run():
        stop = asyncio.Event()
        await bridge_server.serve(
            port=_free_port(), on_ready=lambda info: stop.set(), stop=stop
        )

    asyncio.run(run())
    assert capsys.readouterr().out == ""


def test_a_taken_port_raises_and_maps_to_port_in_use(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        with pytest.raises(OSError) as caught:
            asyncio.run(bridge_server.serve(port=port, on_ready=lambda info: None))
        assert bridge_server.fatal_kind_for(caught.value) == "PORT_IN_USE"
    finally:
        blocker.close()


def test_fatal_kind_recognises_every_address_in_use_shape():
    for code in (10048, 48, 98):
        assert bridge_server.fatal_kind_for(OSError(code, "in use")) == "PORT_IN_USE"
    windows_style = OSError("in use")
    windows_style.winerror = 10048
    assert bridge_server.fatal_kind_for(windows_style) == "PORT_IN_USE"
    assert bridge_server.fatal_kind_for(OSError(errno.EACCES, "denied")) == "CRASH"
    assert bridge_server.fatal_kind_for(RuntimeError("boom")) == "CRASH"


def test_console_banner_prints_the_pairing_details(capsys):
    bridge_server.console_banner({
        "ips": ["192.168.1.42"],
        "port": 8765,
        "token": "tok123",
        "cert_fp": "A1:B2:C3",
        "pairing_uri": "starcompanion://pair?v=2",
    })
    out = capsys.readouterr().out
    assert "StarCompanion Desktop Bridge" in out
    assert "192.168.1.42" in out
    assert "8765" in out
    assert "tok123" in out
    assert "A1:B2:C3" in out


def test_console_banner_lists_every_ip_when_there_are_several(capsys):
    bridge_server.console_banner({
        "ips": ["192.168.1.42", "10.0.0.5"],
        "port": 8765, "token": "t", "cert_fp": "f",
        "pairing_uri": "starcompanion://pair",
    })
    out = capsys.readouterr().out
    assert "192.168.1.42" in out and "10.0.0.5" in out


def test_client_count_is_reported_on_connect_and_disconnect(monkeypatch):
    counts = []
    monkeypatch.setattr(bridge_server, "_on_clients", counts.append)
    bridge_server._connections.clear()
    try:
        bridge_server._track_connection("ws-a")
        bridge_server._track_connection("ws-b")
        bridge_server._untrack_connection("ws-a")
        assert counts == [1, 2, 1]
    finally:
        bridge_server._connections.clear()


def test_telemetry_master_switch_defaults_on_and_toggles():
    try:
        assert bridge_server.telemetry_enabled() is True
        bridge_server.set_telemetry_enabled(False)
        assert bridge_server.telemetry_enabled() is False
    finally:
        bridge_server.set_telemetry_enabled(True)


def test_a_disabled_master_switch_makes_subscribe_fail_cleanly(monkeypatch):
    # With telemetry off the handler must behave exactly as it does when no log
    # is found, so the app shows its existing distinct state rather than hanging
    # on a subscription that will never produce frames.
    monkeypatch.setattr(bridge_server, "_log_path_override", None)
    bridge_server.set_telemetry_enabled(False)
    try:
        assert bridge_server._resolve_log_path() is None
    finally:
        bridge_server.set_telemetry_enabled(True)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_serve_refactor.py -v`
Expected: FAIL — `AttributeError: module 'bridge_server' has no attribute 'serve'`

- [ ] **Step 3: Refactor `main` into `serve` plus `console_banner`**

Replace `bridge_server.py:877-918` (the `# ── Entry point ──` header through the end of `main`) with:

```python
# ── Entry point ───────────────────────────────────────────────────────────────
async def serve(
    port: int = PORT,
    on_ready=None,
    on_clients=None,
    stop: asyncio.Event | None = None,
) -> None:
    """Bind and serve. Prints nothing — the caller decides how to present it.

    `on_ready` is called once with a plain dict of pairing details; keeping it a
    dict rather than an event type means this module never imports the GUI, so
    console mode still runs if core/ and ui/ are absent.

    `on_ready` and `on_clients` are called on this thread. A GUI caller must do
    nothing in them but queue an event.
    """
    global _on_clients
    if on_clients is not None:
        _on_clients = on_clients

    ips = _local_ips()
    ssl_ctx, cert_path = _ensure_ssl_context(ips)
    token = _ensure_token()

    # Bind before reporting anything, so a failure to claim the port surfaces as
    # an error instead of arriving after a "ready" announcement.
    server = await websockets.serve(
        make_handler(token), "0.0.0.0", port,
        ssl=ssl_ctx, max_size=MAX_MESSAGE_BYTES,
        ping_interval=PING_INTERVAL, ping_timeout=PING_TIMEOUT,
    )

    fingerprint = _cert_fingerprint(cert_path)
    info = {
        "ips": ips,
        "port": port,
        "token": token,
        "cert_fp": fingerprint,
        "pairing_uri": _pairing_uri(ips, port, token, _cert_fingerprint_b64(cert_path)),
    }
    if on_ready is not None:
        on_ready(info)

    async with server:
        if stop is None:
            await asyncio.Future()
        else:
            await stop.wait()


def console_banner(info: dict) -> None:
    """The startup banner and terminal QR, for --console mode."""
    ips = info["ips"]
    print()
    print("=" * 60)
    print("  StarCompanion Desktop Bridge  (WSS/TLS)")
    if len(ips) == 1:
        print(f"  Enter this IP in the app:  {ips[0]}")
    else:
        print("  Enter one of these IPs in the app (try the first):")
        for ip in ips:
            print(f"      {ip}")
    print(f"  Port: {info['port']}")
    print(f"  Pairing token: {info['token']}")
    print(f"  Cert SHA-256: {info['cert_fp']}")
    print("=" * 60)
    print()

    if _print_qr(info["pairing_uri"]):
        print()
        print("  Scan this in the StarCompanion app to pair.")
    else:
        # No QR (piped output, or a console without ANSI support) — the URI
        # still pairs if the app can take it by paste or deep link.
        print(f"  Pairing link: {info['pairing_uri']}")
    print()
    logging.info("Waiting for app to connect... (Star Citizen must be the active window)")


def fatal_kind_for(exc: BaseException) -> str:
    """Classify a server-thread exception for the GUI's failure row."""
    if isinstance(exc, OSError):
        # WSAEADDRINUSE surfaces as winerror or as errno depending on how
        # asyncio re-raises it; EADDRINUSE is 48 on macOS, 98 on Linux.
        if getattr(exc, "winerror", None) == 10048 or exc.errno in (10048, 48, 98):
            return "PORT_IN_USE"
    return "CRASH"


async def main(port: int = PORT) -> None:
    await serve(port=port, on_ready=console_banner)
```

- [ ] **Step 4: Add the connection-count hook**

Replace `bridge_server.py:653` (`_connections: dict = {}`) with:

```python
_connections: dict = {}

# Set by serve() in GUI mode so the header can show a live count. Console mode
# leaves it None and pays nothing.
_on_clients = None


def _track_connection(ws) -> None:
    _connections[ws] = True
    if _on_clients is not None:
        _on_clients(len(_connections))


def _untrack_connection(ws) -> None:
    _connections.pop(ws, None)
    if _on_clients is not None:
        _on_clients(len(_connections))
```

Then in `make_handler`, change `_connections[ws] = True` (line 700) to `_track_connection(ws)`, change the eviction's `_connections.pop(oldest, None)` (line 692) to `_untrack_connection(oldest)`, and change the `finally` block's `_connections.pop(ws, None)` (line 837) to `_untrack_connection(ws)`.

- [ ] **Step 5: Add the telemetry master switch**

After the `_log_path_override` declaration at `bridge_server.py:79`, add:

```python
# Master switch for the whole telemetry feature, set from the GUI's settings.
# This sits above the existing per-connection subscribe opt-in: with it off, the
# bridge behaves exactly as it does when no Game.log can be found, so the app
# shows its existing "log not found" state instead of waiting for frames that
# will never come.
_telemetry_enabled = True


def set_telemetry_enabled(enabled: bool) -> None:
    global _telemetry_enabled
    _telemetry_enabled = bool(enabled)


def telemetry_enabled() -> bool:
    return _telemetry_enabled


def _resolve_log_path():
    """The Game.log to tail, or None when disabled or not found."""
    if not _telemetry_enabled:
        return None
    return _log_path_override or default_log_path()
```

Then in `make_handler`, replace line 707 — `log_path = _log_path_override or default_log_path()` — with:

```python
        log_path = _resolve_log_path()
```

- [ ] **Step 6: Move logging setup behind a call**

Replace `bridge_server.py:171-183` with:

```python
# ── Logging ─────────────────────────────────────────────────────────────────────
# The Windows console defaults to cp1252, which raises on any non-ASCII text in
# a log line. Force UTF-8 so logging can never crash on a stray character.
# Under a PyInstaller --windowed build these streams are None, hence the suppress.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def configure_console_logging() -> None:
    """Console mode's logging: timestamped lines on stdout, as before."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
```

Then in the `if __name__ == "__main__":` block at line 992, call `configure_console_logging()` before `asyncio.run(main(...))`. Task 10 rewires this block properly; this keeps the tree working in between.

- [ ] **Step 7: Run the new tests and the whole existing suite**

Run: `python -m pytest tests/ -v`
Expected: all new tests pass and every pre-existing test still passes. The existing suite passing untouched is the guard that server behavior did not change.

- [ ] **Step 8: Commit**

```bash
git add bridge_server.py tests/test_serve_refactor.py
git commit -m "refactor: split serving from printing, ahead of the GUI

serve() binds and runs without printing; console_banner() holds every print
main() used to make. Adds a connection-count hook, a telemetry master switch,
and fatal_kind_for(), which the GUI needs to show why a start failed instead
of vanishing. Console mode behaviour is unchanged."
```

---

### Task 6: Theme, app icon, and the QR bitmap

**Files:**
- Create: `ui/theme.py`
- Create: `tools/make_icon.py`
- Create: `assets/bridge.ico` (generated by the script, committed)
- Test: `tests/test_qr_image.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ui/theme.py`: `BG`, `PANEL`, `ACCENT`, `TEXT`, `MUTED`, `OK`, `WARN`, `ERROR`, `DOT_IDLE` colour constants; `FONT_UI`, `FONT_HEADER`, `FONT_MONO` tuples; `apply_theme(root) -> None`; `enable_dpi_awareness() -> None`; `icon_path() -> Path | None`
  - `ui/theme.py`: `qr_rows(data: str, scale: int = 4) -> list[str]` — one string of space-separated hex colours per pixel row, the exact argument shape `tk.PhotoImage.put` takes
  - `tools/make_icon.py`: writes `assets/bridge.ico`, runnable as `python tools/make_icon.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_qr_image.py`:

```python
from ui.theme import qr_rows


def test_rows_are_square_and_scaled():
    rows = qr_rows("starcompanion://pair?v=2", scale=4)
    assert rows, "expected at least one row"
    width = len(rows[0].split())
    assert width == len(rows), "a QR code is square"
    assert width % 4 == 0, "every module is `scale` pixels wide"


def test_scale_multiplies_both_dimensions():
    small = qr_rows("hello", scale=1)
    big = qr_rows("hello", scale=3)
    assert len(big) == len(small) * 3
    assert len(big[0].split()) == len(small[0].split()) * 3


def test_only_black_and_white_pixels_are_emitted():
    colors = {c for row in qr_rows("hello", scale=2) for c in row.split()}
    assert colors <= {"#000000", "#ffffff"}


def test_a_quiet_border_surrounds_the_code():
    rows = qr_rows("hello", scale=1)
    assert set(rows[0].split()) == {"#ffffff"}
    assert set(rows[-1].split()) == {"#ffffff"}


def test_different_payloads_produce_different_bitmaps():
    assert qr_rows("one", scale=2) != qr_rows("two", scale=2)


def test_the_same_payload_is_stable():
    assert qr_rows("same", scale=2) == qr_rows("same", scale=2)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_qr_image.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.theme'`

- [ ] **Step 3: Write the theme module**

Create `ui/theme.py`:

```python
"""Palette, fonts, ttk styling, and the QR bitmap.

ttk's default theme ignores most colour options on most platforms, so the styles
below are built on the "clam" theme, which honours them. Everything visual lives
here so a colour is defined once.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from tkinter import ttk

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_qr_image.py -v`
Expected: 6 passed

- [ ] **Step 5: Write the icon generator**

Create `tools/make_icon.py`:

```python
"""Generates assets/bridge.ico — a placeholder until real artwork exists.

Writes the ICO container and its BMP payloads by hand rather than adding Pillow
for a one-off asset. Run: python tools/make_icon.py
"""

from __future__ import annotations

import struct
from pathlib import Path

BG = (0x14, 0x18, 0x1D)
RING = (0x4A, 0xA8, 0xFF)
SIZES = (16, 32, 48, 64, 256)


def _pixel(x: int, y: int, size: int) -> tuple[int, int, int, int]:
    """A blue ring with a filled centre dot on a transparent field."""
    cx = cy = (size - 1) / 2
    radius = (x - cx) ** 2 + (y - cy) ** 2
    outer = (size * 0.47) ** 2
    inner = (size * 0.33) ** 2
    core = (size * 0.16) ** 2
    if radius <= core:
        return (*RING[::-1], 255)
    if inner < radius <= outer:
        return (*RING[::-1], 255)
    if radius <= outer:
        return (*BG[::-1], 255)
    return (0, 0, 0, 0)


def _bmp(size: int) -> bytes:
    header = struct.pack(
        "<IiiHHIIiiII",
        40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0,
    )
    rows = []
    for y in range(size - 1, -1, -1):  # BMP rows run bottom-up
        rows.append(b"".join(bytes(_pixel(x, y, size)) for x in range(size)))
    mask_row = b"\x00" * (((size + 31) // 32) * 4)
    return header + b"".join(rows) + mask_row * size


def build(path: Path) -> None:
    images = [(size, _bmp(size)) for size in SIZES]
    offset = 6 + 16 * len(images)
    out = [struct.pack("<HHH", 0, 1, len(images))]
    for size, payload in images:
        out.append(struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size, 0 if size >= 256 else size,
            0, 0, 1, 32, len(payload), offset,
        ))
        offset += len(payload)
    out.extend(payload for _, payload in images)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(out))
    print(f"wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    build(Path(__file__).resolve().parent.parent / "assets" / "bridge.ico")
```

- [ ] **Step 6: Generate the icon and check it loads**

Run:
```bash
python tools/make_icon.py
python -c "
import struct, pathlib
raw = pathlib.Path('assets/bridge.ico').read_bytes()
reserved, kind, count = struct.unpack('<HHH', raw[:6])
assert (reserved, kind) == (0, 1), 'not an ICO'
assert count == 5, count
print('ok:', count, 'images,', len(raw), 'bytes')
"
```
Expected: `ok: 5 images, ... bytes`

- [ ] **Step 7: Commit**

```bash
git add ui/theme.py tools/make_icon.py assets/bridge.ico tests/test_qr_image.py
git commit -m "feat(ui): add the dark theme, app icon, and QR bitmap renderer

The QR goes straight from qrcode's boolean matrix into PhotoImage.put, which
is what keeps Pillow out of the dependency list. The icon is generated rather
than drawn, as a placeholder for real artwork."
```

---

### Task 7: Widgets

**Files:**
- Create: `ui/widgets.py`
- Test: `tests/test_widgets.py`

**Interfaces:**
- Consumes: `ui.theme` constants, `ui.log_handler.color_for_level`.
- Produces:
  - `StatusDot(parent)` with `set(color: str) -> None`
  - `CopyRow(parent, label: str, value: str = "", masked: bool = False)` with `set_value(value: str) -> None`, `value -> str`, and `mask(value: str) -> str` as a module function
  - `ActivityPane(parent, capacity: int = 500)` with `append(ts: str, level: int, text: str) -> None`, `clear() -> None`, `note(text: str) -> None`, `paused -> bool`, and `lines -> list[str]`
  - `mask(value: str) -> str` — module-level, testable without Tk
  - `Tooltip(widget, text: str)`

Every Tk-touching test in this task is guarded by the shared `tk_root` fixture, which skips when no display is available.

- [ ] **Step 1: Write the shared display fixture**

Create `tests/conftest.py` if it does not exist; otherwise append:

```python
import pytest


@pytest.fixture
def tk_root():
    """A hidden Tk root, or a skip when there is no display.

    CI and the macOS dev machine may both lack one; the logic these widgets
    wrap is tested separately so a skip here costs little.
    """
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except Exception as exc:  # noqa: BLE001 - any display failure is a skip
        pytest.skip(f"no display available: {exc}")
    root.withdraw()
    yield root
    root.destroy()
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_widgets.py`:

```python
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
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/test_widgets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.widgets'`

- [ ] **Step 4: Write the implementation**

Create `ui/widgets.py`:

```python
"""The four composite widgets the window is built from."""

from __future__ import annotations

import tkinter as tk
from collections import deque
from tkinter import ttk

from ui.log_handler import color_for_level
from ui.theme import ACCENT, BG, DOT_IDLE, FONT_MONO, FONT_UI, MUTED, PANEL, TEXT


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


class StatusDot(tk.Canvas):
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


class CopyRow(ttk.Frame):
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


class ActivityPane(ttk.Frame):
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

        self._text.tag_configure("muted", foreground=MUTED)
        self._text.tag_configure("accent", foreground=ACCENT)

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
        self._lines.append(line)
        self._text.configure(state="normal")
        self._text.insert("end", line + "\n", tag)
        # Trim in lockstep with the deque so the widget and `lines` never disagree.
        excess = int(self._text.index("end-1c").split(".")[0]) - 1 - self._capacity
        if excess > 0:
            self._text.delete("1.0", f"{excess + 1}.0")
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_widgets.py -v`
Expected: 11 passed, or 9 passed / 2 skipped if no display — the two `mask` tests must pass regardless.

- [ ] **Step 6: Commit**

```bash
git add ui/widgets.py tests/test_widgets.py tests/conftest.py
git commit -m "feat(ui): add the status dot, copy rows, and activity pane

Copy always yields the full value even when the row is masked, and the pane
only follows the tail when the user was already at it."
```

---

### Task 8: Windows autostart

**Files:**
- Create: `core/autostart.py`
- Test: `tests/test_autostart.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"`, `VALUE_NAME = "StarCompanionBridge"`
  - `supported() -> bool` — True only on Windows and only when frozen
  - `reason_unsupported() -> str`
  - `command_for(exe: Path) -> str`
  - `read_entry() -> str | None`
  - `enable(exe: Path) -> None`, `disable() -> None`
  - `is_stale(exe: Path) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_autostart.py`:

```python
import sys
from pathlib import Path

import pytest

from core import autostart


class FakeRegistry:
    """Stand-in for winreg, so this runs on macOS."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.deleted = []

    class _Key:
        def __init__(self, registry):
            self.registry = registry

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, path, reserved=0, access=None):  # noqa: N802
        return self._Key(self)

    def CreateKeyEx(self, root, path, reserved=0, access=None):  # noqa: N802
        return self._Key(self)

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]
        self.deleted.append(name)


@pytest.fixture
def registry(monkeypatch):
    fake = FakeRegistry()
    monkeypatch.setattr(autostart, "_winreg", lambda: fake)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    return fake


def test_unsupported_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert autostart.supported() is False
    assert "Windows" in autostart.reason_unsupported()


def test_unsupported_when_running_as_a_script(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert autostart.supported() is False
    # There is no stable path to register for a .py invocation.
    assert "exe" in autostart.reason_unsupported()


def test_supported_when_frozen_on_windows(registry):
    assert autostart.supported() is True


def test_the_command_is_quoted_for_paths_with_spaces():
    got = autostart.command_for(Path(r"C:\Program Files\SC\StarCompanionBridge.exe"))
    assert got == r'"C:\Program Files\SC\StarCompanionBridge.exe"'


def test_enable_writes_the_run_value(registry):
    exe = Path(r"C:\apps\StarCompanionBridge.exe")
    autostart.enable(exe)
    assert registry.values["StarCompanionBridge"] == r'"C:\apps\StarCompanionBridge.exe"'


def test_read_entry_returns_none_when_absent(registry):
    assert autostart.read_entry() is None


def test_read_entry_returns_the_written_value(registry):
    autostart.enable(Path(r"C:\apps\StarCompanionBridge.exe"))
    assert autostart.read_entry() == r'"C:\apps\StarCompanionBridge.exe"'


def test_disable_removes_the_value(registry):
    autostart.enable(Path(r"C:\apps\StarCompanionBridge.exe"))
    autostart.disable()
    assert "StarCompanionBridge" not in registry.values
    assert autostart.read_entry() is None


def test_disable_is_harmless_when_there_is_nothing_to_remove(registry):
    autostart.disable()  # must not raise
    assert autostart.read_entry() is None


def test_a_moved_exe_makes_the_entry_stale(registry):
    autostart.enable(Path(r"C:\old\StarCompanionBridge.exe"))
    assert autostart.is_stale(Path(r"C:\new\StarCompanionBridge.exe")) is True
    assert autostart.is_stale(Path(r"C:\old\StarCompanionBridge.exe")) is False


def test_no_entry_is_not_stale(registry):
    assert autostart.is_stale(Path(r"C:\apps\StarCompanionBridge.exe")) is False


def test_a_registry_error_is_swallowed_rather_than_crashing_the_settings_panel(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    class Broken(FakeRegistry):
        def CreateKeyEx(self, *a, **k):  # noqa: N802
            raise OSError("access denied")

        def OpenKey(self, *a, **k):  # noqa: N802
            raise OSError("access denied")

    monkeypatch.setattr(autostart, "_winreg", lambda: Broken())
    autostart.enable(Path(r"C:\apps\x.exe"))  # must not raise
    assert autostart.read_entry() is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_autostart.py -v`
Expected: FAIL — `ImportError: cannot import name 'autostart' from 'core'`

- [ ] **Step 3: Write the implementation**

Create `core/autostart.py`:

```python
"""Start-with-Windows, via the per-user Run key.

HKCU rather than HKLM deliberately: it needs no elevation, and a keystroke
bridge has no business asking for admin. Every registry call is best-effort --
a locked-down machine should grey the checkbox out, not crash the panel.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "StarCompanionBridge"


def _winreg():
    """Imported through a function so tests can substitute a fake on macOS."""
    import winreg

    return winreg


def supported() -> bool:
    return sys.platform == "win32" and getattr(sys, "frozen", False)


def reason_unsupported() -> str:
    if sys.platform != "win32":
        return "Start with Windows is only available on Windows."
    return "Only available for the packaged exe — a .py script has no fixed path to register."


def command_for(exe: Path) -> str:
    """The Run value: always quoted, since the exe usually sits under a path with spaces."""
    return f'"{Path(exe)}"'


def read_entry() -> str | None:
    if sys.platform != "win32":
        return None
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return value
    except (FileNotFoundError, OSError):
        return None


def enable(exe: Path) -> None:
    if sys.platform != "win32":
        return
    winreg = _winreg()
    with contextlib.suppress(OSError):
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command_for(exe))


def disable() -> None:
    if sys.platform != "win32":
        return
    winreg = _winreg()
    with contextlib.suppress(FileNotFoundError, OSError):
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)


def is_stale(exe: Path) -> bool:
    """True when an entry exists but points somewhere other than this exe."""
    entry = read_entry()
    if entry is None:
        return False
    return entry.strip().strip('"').casefold() != str(Path(exe)).casefold()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_autostart.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add core/autostart.py tests/test_autostart.py
git commit -m "feat(ui): add start-with-Windows via the per-user Run key

HKCU rather than HKLM: no elevation, because a keystroke bridge has no
business asking for admin. Registry failures grey the control out rather than
crashing the settings panel."
```

---

### Task 9: The window

**Files:**
- Create: `ui/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `core.config.Config`/`save`, `core.events` (all), `core.status.StatusModel`, `ui.theme`, `ui.widgets`, `ui.log_handler.configure_logging`, and `bridge_server.serve`/`fatal_kind_for`/`set_telemetry_enabled`.
- Produces:
  - `ServerThread(port: int, bus: EventBus, stop_timeout: float = 3.0)` with `start()`, `stop(timeout: float = 3.0) -> bool`, `alive -> bool`
  - `BridgeApp(cfg: Config, bus: EventBus, app_dir: Path, root: tk.Tk | None = None, autostart_server: bool = True)` with `run() -> None`, `drain_once() -> None`, `restart() -> None`, `retry() -> None`, `on_close() -> None`, `apply_settings(cfg: Config) -> None`, `start_server() -> None`, `stop_server(timeout: float = 3.0) -> bool`, `select_ip(ip: str) -> None`, `toggle_qr() -> None`, `open_settings() -> None`. `root` and `autostart_server` exist so tests can build the window against a fixture root without launching a server.
  - `run_gui(cfg: Config, app_dir: Path) -> None` — builds the bus, configures logging, runs the app
  - `DRAIN_INTERVAL_MS = 100`, `DRAIN_LIMIT = 200`

- [ ] **Step 1: Write the failing test**

Create `tests/test_app.py`:

```python
import logging
import threading

import pytest

from core.config import Config
from core.events import (
    FATAL_CRASH,
    FATAL_PORT_IN_USE,
    ClientCount,
    EventBus,
    Fatal,
    LogLine,
    Ready,
    TelemetryState,
)
from core.status import CONNECTED, ERROR, LISTENING, PORT_IN_USE
from ui.app import DRAIN_LIMIT, BridgeApp, ServerThread


def _ready():
    return Ready(ips=("192.168.1.42", "10.0.0.5"), port=8765, token="abcdef1234567890",
                 cert_fp="A1:B2:C3", pairing_uri="starcompanion://pair?v=2")


@pytest.fixture
def app(tk_root, tmp_path):
    bus = EventBus()
    instance = BridgeApp(Config(), bus, tmp_path, root=tk_root, autostart_server=False)
    yield instance


def test_ready_fills_the_pair_card(app):
    app.bus.emit(_ready())
    app.drain_once()
    assert app.status.status == LISTENING
    assert app.ip_row.value == "192.168.1.42"
    assert app.port_row.value == "8765"
    assert app.token_row.value == "abcdef1234567890"
    assert app.token_row.displayed == "abcd…7890"
    assert app.cert_row.value == "A1:B2:C3"


def test_several_ips_become_a_selector_and_switching_redraws_the_qr(app):
    app.bus.emit(_ready())
    app.drain_once()
    assert app.ip_choices == ["192.168.1.42", "10.0.0.5"]
    first = app.qr_payload
    app.select_ip("10.0.0.5")
    assert app.ip_row.value == "10.0.0.5"
    assert app.qr_payload != first
    assert "10.0.0.5" in app.qr_payload


def test_a_single_ip_offers_no_selector(app):
    app.bus.emit(Ready(ips=("192.168.1.42",), port=8765, token="t",
                       cert_fp="f", pairing_uri="starcompanion://pair"))
    app.drain_once()
    assert app.ip_choices == ["192.168.1.42"]


def test_client_count_reaches_the_header(app):
    app.bus.emit(_ready())
    app.bus.emit(ClientCount(2))
    app.drain_once()
    assert app.status.status == CONNECTED
    assert app.header_label.cget("text") == "Connected — 2 apps"
    assert app.dot.color == "#4ade80"


def test_log_lines_reach_the_activity_pane(app):
    app.bus.emit(LogLine(ts="21:04:11", level=logging.INFO, text="App connected"))
    app.drain_once()
    assert app.activity.lines == ["21:04:11  App connected"]


def test_telemetry_state_reaches_the_strip(app):
    app.bus.emit(TelemetryState(found=True, path="F:/SC/Game.log", feeds=2))
    app.drain_once()
    assert "2 feeds" in app.telemetry_label.cget("text")
    app.bus.emit(TelemetryState(found=False, path=None, feeds=0))
    app.drain_once()
    assert "not found" in app.telemetry_label.cget("text").lower()


def test_dropped_events_are_reported_rather_than_hidden(app):
    small = EventBus(maxsize=1)
    app.bus = small
    small.emit(LogLine(ts="21:04:11", level=logging.INFO, text="kept"))
    small.emit(LogLine(ts="21:04:12", level=logging.INFO, text="dropped"))
    app.drain_once()
    assert any("dropped" in line for line in app.activity.lines)


def test_the_drain_is_bounded_per_tick(app):
    for i in range(DRAIN_LIMIT + 50):
        app.bus.emit(LogLine(ts="21:04:11", level=logging.INFO, text=f"line {i}"))
    app.drain_once()
    assert len(app.activity.lines) == DRAIN_LIMIT


def test_port_in_use_shows_the_failure_row_with_both_remedies(app):
    app.bus.emit(Fatal(kind=FATAL_PORT_IN_USE, message="port 8765 is already in use"))
    app.drain_once()
    assert app.status.status == PORT_IN_USE
    assert app.failure_visible is True
    assert app.retry_button is not None
    assert app.settings_shortcut_visible is True


def test_a_crash_shows_the_failure_row_without_the_settings_shortcut(app):
    app.bus.emit(Fatal(kind=FATAL_CRASH, message="boom"))
    app.drain_once()
    assert app.status.status == ERROR
    assert app.failure_visible is True
    assert app.settings_shortcut_visible is False
    assert "boom" in app.failure_label.cget("text")


def test_the_window_stays_open_after_a_fatal(app):
    app.bus.emit(Fatal(kind=FATAL_CRASH, message="boom"))
    app.drain_once()
    assert app.root.winfo_exists()


def test_retry_clears_the_failure_and_restarts(app, monkeypatch):
    restarts = []
    monkeypatch.setattr(app, "start_server", lambda: restarts.append(1))
    app.bus.emit(Fatal(kind=FATAL_CRASH, message="boom"))
    app.drain_once()
    app.retry()
    assert app.status.fatal is False
    assert app.failure_visible is False
    assert restarts == [1]


def test_restart_keeps_the_token_and_clears_the_card(app, monkeypatch):
    monkeypatch.setattr(app, "start_server", lambda: None)
    monkeypatch.setattr(app, "stop_server", lambda timeout=3.0: True)
    app.bus.emit(_ready())
    app.drain_once()
    app.restart()
    assert app.token_row.value == ""  # cleared until the new Ready arrives
    assert app.status.status == "STARTING"


def test_apply_settings_persists_and_restarts_only_when_the_port_changed(app, monkeypatch):
    restarts = []
    monkeypatch.setattr(app, "restart", lambda: restarts.append(1))
    changed = Config(port=9100, telemetry_enabled=False)
    app.apply_settings(changed)
    from core.config import load

    saved, _ = load(app.app_dir)
    assert saved.port == 9100
    assert saved.telemetry_enabled is False
    assert restarts == [1]

    restarts.clear()
    app.apply_settings(Config(port=9100, telemetry_enabled=True))
    assert restarts == []  # telemetry applies live, no restart needed


def test_server_thread_reports_port_in_use_as_a_fatal_event(tmp_path, monkeypatch):
    import socket

    import bridge_server

    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    bus = EventBus()
    try:
        thread = ServerThread(port=port, bus=bus)
        thread.start()
        thread.join(timeout=15)
        assert not thread.alive
    finally:
        blocker.close()
    fatals = [e for e in bus.drain(limit=1000)[0] if isinstance(e, Fatal)]
    assert fatals and fatals[0].kind == FATAL_PORT_IN_USE


def test_server_thread_emits_ready_and_stops_cleanly(tmp_path, monkeypatch):
    import socket

    import bridge_server

    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    bus = EventBus()
    thread = ServerThread(port=port, bus=bus)
    thread.start()
    ready = None
    for _ in range(150):
        events, _ = bus.drain(limit=100)
        ready = next((e for e in events if isinstance(e, Ready)), ready)
        if ready is not None:
            break
        threading.Event().wait(0.1)
    assert ready is not None, "server never reported Ready"
    assert thread.stop(timeout=10) is True
    assert not thread.alive


def test_server_thread_is_a_daemon_so_a_hung_tailer_cannot_outlive_the_window(tmp_path):
    thread = ServerThread(port=1, bus=EventBus())
    assert thread.daemon is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.app'`

- [ ] **Step 3: Write the implementation**

Create `ui/app.py`:

```python
"""The window: layout, the drain loop, and the server thread's lifecycle.

Tk owns the main thread because Windows requires Tk calls to come from the
thread that created the widgets. The server therefore runs in a daemon worker
whose only outward channel is EventBus.emit -- nothing in ServerThread may
touch a widget.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import ttk

import bridge_server
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
    ACCENT,
    BG,
    ERROR,
    MUTED,
    OK,
    PANEL,
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
        self._ready_to_stop.wait(timeout=1.0)
        if self._loop is not None and self._stop is not None:
            with __import__("contextlib").suppress(RuntimeError):
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
        enable_dpi_awareness()
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
        self.cert_row = CopyRow(rows, "Cert")
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
        self.drain_once()
        self.root.after(DRAIN_INTERVAL_MS, self._tick)

    def drain_once(self) -> None:
        events, dropped = self.bus.drain(limit=DRAIN_LIMIT)
        for event in events:
            self._handle(event)
        if dropped:
            plural = "event" if dropped == 1 else "events"
            self.activity.note(f"… {dropped} {plural} dropped")

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
        self.header_label.configure(text=self.status.text)
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

    def retry(self) -> None:
        self.status.reset()
        self._hide_failure()
        self._refresh_status()
        self.start_server()

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start_server(self) -> None:
        bridge_server.set_telemetry_enabled(self.cfg.telemetry_enabled)
        if self.cfg.log_path:
            bridge_server._log_path_override = Path(self.cfg.log_path)
        else:
            bridge_server._log_path_override = None
        self.server = ServerThread(port=self.cfg.port, bus=self.bus)
        self.server.start()

    def stop_server(self, timeout: float = 3.0) -> bool:
        if self.server is None:
            return True
        stopped = self.server.stop(timeout=timeout)
        self.server = None
        return stopped

    def restart(self) -> None:
        self.stop_server()
        self.status.reset()
        self._hide_failure()
        for row in (self.ip_row, self.port_row, self.token_row, self.cert_row):
            row.set_value("")
        self.ip_selector.grid_remove()
        self._ips = []
        self._refresh_status()
        self.start_server()

    def apply_settings(self, new_cfg: Config) -> None:
        """Persist the panel's result and apply as much of it as we can live."""
        port_changed = new_cfg.port != self.cfg.port
        log_changed = new_cfg.log_path != self.cfg.log_path
        self.cfg = new_cfg
        save(self.cfg, self.app_dir)
        bridge_server.set_telemetry_enabled(self.cfg.telemetry_enabled)
        if port_changed or log_changed:
            # The listener is bound to a port and each connection builds its own
            # tailer at accept time, so both of these need a fresh server.
            self.restart()

    def open_settings(self) -> None:
        from ui.settings import SettingsDialog

        SettingsDialog(self.root, self.cfg, self.app_dir, on_save=self.apply_settings)

    def on_close(self) -> None:
        self.cfg.window_height = max(self.root.winfo_height(), MIN_HEIGHT)
        save(self.cfg, self.app_dir)
        if not self.stop_server(timeout=3.0):
            logging.warning("Server thread did not stop in time — exiting anyway.")
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_gui(cfg: Config, app_dir: Path, config_warning: str | None = None) -> None:
    bus = EventBus()
    configure_logging(bus, log_file=Path(app_dir) / "bridge.log")
    if config_warning:
        logging.warning(config_warning)
    BridgeApp(cfg, bus, app_dir).run()
```

Replace the `__import__("contextlib")` in `ServerThread.stop` with a top-level `import contextlib` and `contextlib.suppress(RuntimeError)` — the inline form above is only to keep the snippet readable.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_app.py -v`
Expected: all pass. The `tk_root`-dependent tests skip when no display is available; the three `ServerThread` tests must pass regardless.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest tests/ -v`
Expected: every test passes, including the four pre-existing files untouched.

- [ ] **Step 6: Commit**

```bash
git add ui/app.py tests/test_app.py
git commit -m "feat(ui): add the bridge window and the server thread that feeds it

A start failure now paints a red status, states the reason, and offers Retry
with the window still open. Previously the exe printed the error and closed,
taking the explanation with it. Switching between multiple local IPs redraws
the QR, which replaces the README's 'try the first one' advice."
```

---

### Task 10: Settings panel

**Files:**
- Create: `ui/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: `core.config.Config`, `core.autostart`, `ui.theme`, `ui.widgets.Tooltip`, `telemetry.tailer.default_log_path`.
- Produces:
  - `validate_port(value: str, current_port: int) -> str | None` — an error message, or None
  - `validate_log_path(value: str) -> str | None`
  - `SettingsDialog(parent, cfg: Config, app_dir: Path, on_save: Callable[[Config], None])` with `result -> Config | None`, `save() -> bool`, `cancel() -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_settings.py`:

```python
import socket

import pytest

from core.config import Config
from ui.settings import validate_log_path, validate_port


def test_a_free_port_validates():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert validate_port(str(free), current_port=8765) is None


def test_a_non_numeric_port_is_rejected():
    assert "number" in validate_port("eight", current_port=8765).lower()


def test_an_empty_port_is_rejected():
    assert validate_port("", current_port=8765) is not None


def test_a_privileged_or_out_of_range_port_is_rejected():
    assert "1024" in validate_port("80", current_port=8765)
    assert validate_port("70000", current_port=8765) is not None
    assert validate_port("-1", current_port=8765) is not None


def test_a_taken_port_is_rejected_before_we_restart_into_a_dead_state():
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    taken = blocker.getsockname()[1]
    try:
        assert "in use" in validate_port(str(taken), current_port=8765).lower()
    finally:
        blocker.close()


def test_the_port_we_are_already_serving_on_is_not_reported_as_in_use():
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    ours = blocker.getsockname()[1]
    try:
        # It is in use *by us*. Rejecting it would make the panel unable to save
        # any unrelated change without also moving the port.
        assert validate_port(str(ours), current_port=ours) is None
    finally:
        blocker.close()


def test_an_empty_log_path_means_auto_detect():
    assert validate_log_path("") is None


def test_a_missing_log_path_is_rejected(tmp_path):
    message = validate_log_path(str(tmp_path / "nope" / "Game.log"))
    assert message is not None and "does not exist" in message


def test_a_directory_is_not_a_log_file(tmp_path):
    assert validate_log_path(str(tmp_path)) is not None


def test_an_existing_readable_log_validates(tmp_path):
    log = tmp_path / "Game.log"
    log.write_text("<2026-08-14T00:00:00.000Z> ok\n")
    assert validate_log_path(str(log)) is None


def test_an_unreadable_log_is_rejected(tmp_path):
    log = tmp_path / "Game.log"
    log.write_text("x")
    log.chmod(0o000)
    try:
        message = validate_log_path(str(log))
    finally:
        log.chmod(0o644)
    if message is None:
        pytest.skip("running as a user that can read mode-000 files")
    assert "cannot be read" in message


def test_the_dialog_returns_the_edited_config(tk_root, tmp_path):
    from ui.settings import SettingsDialog

    saved = []
    dialog = SettingsDialog(tk_root, Config(port=8765), tmp_path, on_save=saved.append)
    dialog.port_var.set("9100")
    dialog.telemetry_var.set(False)
    assert dialog.save() is True
    assert saved and saved[0].port == 9100
    assert saved[0].telemetry_enabled is False


def test_the_dialog_refuses_to_save_an_invalid_port(tk_root, tmp_path):
    from ui.settings import SettingsDialog

    saved = []
    dialog = SettingsDialog(tk_root, Config(), tmp_path, on_save=saved.append)
    dialog.port_var.set("80")
    assert dialog.save() is False
    assert saved == []
    assert "1024" in dialog.error_label.cget("text")


def test_cancel_saves_nothing(tk_root, tmp_path):
    from ui.settings import SettingsDialog

    saved = []
    dialog = SettingsDialog(tk_root, Config(port=8765), tmp_path, on_save=saved.append)
    dialog.port_var.set("9100")
    dialog.cancel()
    assert saved == []


def test_a_cli_supplied_port_is_shown_but_not_editable(tk_root, tmp_path):
    from ui.settings import SettingsDialog

    cfg = Config(port=7000, port_from_cli=True)
    dialog = SettingsDialog(tk_root, cfg, tmp_path, on_save=lambda c: None)
    assert str(dialog.port_spin.cget("state")) == "disabled"
    assert "--port" in dialog.port_note.cget("text")


def test_a_cli_supplied_log_path_is_shown_but_not_editable(tk_root, tmp_path):
    from ui.settings import SettingsDialog

    cfg = Config(log_path="D:/x/Game.log", log_from_cli=True)
    dialog = SettingsDialog(tk_root, cfg, tmp_path, on_save=lambda c: None)
    assert str(dialog.log_entry.cget("state")) == "disabled"
    assert "--log" in dialog.log_note.cget("text")


def test_autostart_is_disabled_with_a_reason_when_unsupported(tk_root, tmp_path, monkeypatch):
    import sys

    from ui.settings import SettingsDialog

    monkeypatch.setattr(sys, "platform", "darwin")
    dialog = SettingsDialog(tk_root, Config(), tmp_path, on_save=lambda c: None)
    assert str(dialog.autostart_check.cget("state")) == "disabled"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.settings'`

- [ ] **Step 3: Write the implementation**

Create `ui/settings.py`:

```python
"""The settings panel.

Validation happens before Save, not after a restart: a port that is already
taken would otherwise leave the bridge restarted into a dead state, with the
panel already closed and nothing on screen explaining it.
"""

from __future__ import annotations

import contextlib
import socket
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
            exe = Path(__import__("sys").executable)
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
```

Replace `__import__("sys").executable` with a top-level `import sys` and `sys.executable`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_settings.py -v`
Expected: all pass; the `tk_root` tests skip without a display, the eleven validation tests pass regardless.

- [ ] **Step 5: Commit**

```bash
git add ui/settings.py tests/test_settings.py
git commit -m "feat(ui): add the settings panel with pre-save validation

A port is test-bound before Save so an already-taken one is rejected in the
panel, rather than restarting the bridge into a dead state with nothing on
screen to explain it. Fields a CLI flag already controls render disabled and
say which flag, so the panel never misreports what is in effect."
```

---

### Task 11: Wire up the entry point

**Files:**
- Modify: `bridge_server.py:921-1008` (`_parse_options` and `__main__`)
- Test: `tests/test_entrypoint.py`

**Interfaces:**
- Consumes: `core.config.load`/`apply_cli`, `ui.app.run_gui`, `bridge_server.configure_console_logging`.
- Produces:
  - `def _parse_options() -> argparse.Namespace` — now returns the namespace, with `.port: int | None`, `.log_path: str | None`, `.console: bool`
  - `def run_console(port: int) -> None`
  - `def start() -> None` — the whole `__main__` body, so it is testable

- [ ] **Step 1: Write the failing test**

Create `tests/test_entrypoint.py`:

```python
import sys

import pytest

import bridge_server


def test_console_flag_is_recognised(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py", "--console"])
    args = bridge_server._parse_options()
    assert args.console is True


def test_gui_is_the_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py"])
    args = bridge_server._parse_options()
    assert args.console is False


def test_an_unset_port_stays_none_so_the_config_can_supply_it(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py"])
    assert bridge_server._parse_options().port is None


def test_an_explicit_port_is_returned(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py", "--port", "9100"])
    assert bridge_server._parse_options().port == 9100


def test_a_missing_log_path_still_refuses_to_start(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv",
                        ["bridge_server.py", "--log", str(tmp_path / "nope.log")])
    monkeypatch.setattr("builtins.input", lambda *a: "")
    with pytest.raises(SystemExit):
        bridge_server._parse_options()


def test_an_existing_log_path_is_accepted(monkeypatch, tmp_path):
    log = tmp_path / "Game.log"
    log.write_text("x")
    monkeypatch.setattr(sys, "argv", ["bridge_server.py", "--log", str(log)])
    args = bridge_server._parse_options()
    assert args.log_path == str(log)


def test_the_retired_no_auth_optouts_still_refuse(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py", "--insecure-no-auth"])
    monkeypatch.setattr("builtins.input", lambda *a: "")
    with pytest.raises(SystemExit):
        bridge_server._parse_options()


def test_the_env_var_optout_still_refuses(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py"])
    monkeypatch.setenv("STARCOMPANION_INSECURE_NO_AUTH", "1")
    monkeypatch.setattr("builtins.input", lambda *a: "")
    with pytest.raises(SystemExit):
        bridge_server._parse_options()


def test_start_launches_the_gui_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py"])
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    launched = {}
    monkeypatch.setattr("ui.app.run_gui",
                        lambda cfg, app_dir, config_warning=None: launched.update(
                            port=cfg.port, dir=app_dir))
    bridge_server.start()
    assert launched["port"] == 8765
    assert launched["dir"] == tmp_path


def test_start_prefers_the_configured_port_over_the_default(monkeypatch, tmp_path):
    from core.config import Config, save

    save(Config(port=9100), tmp_path)
    monkeypatch.setattr(sys, "argv", ["bridge_server.py"])
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    seen = {}
    monkeypatch.setattr("ui.app.run_gui",
                        lambda cfg, app_dir, config_warning=None: seen.update(port=cfg.port))
    bridge_server.start()
    assert seen["port"] == 9100


def test_a_cli_port_beats_the_configured_one(monkeypatch, tmp_path):
    from core.config import Config, save

    save(Config(port=9100), tmp_path)
    monkeypatch.setattr(sys, "argv", ["bridge_server.py", "--port", "7000"])
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)
    seen = {}
    monkeypatch.setattr("ui.app.run_gui",
                        lambda cfg, app_dir, config_warning=None: seen.update(
                            port=cfg.port, from_cli=cfg.port_from_cli))
    bridge_server.start()
    assert seen["port"] == 7000
    assert seen["from_cli"] is True


def test_console_mode_does_not_touch_the_gui(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["bridge_server.py", "--console"])
    monkeypatch.setattr(bridge_server, "_app_dir", lambda: tmp_path)

    def boom(*a, **k):
        raise AssertionError("console mode must not import or start the GUI")

    monkeypatch.setattr("ui.app.run_gui", boom)
    ran = {}
    monkeypatch.setattr(bridge_server, "run_console", lambda port: ran.update(port=port))
    bridge_server.start()
    assert ran["port"] == 8765
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_entrypoint.py -v`
Expected: FAIL — `AttributeError: module 'bridge_server' has no attribute 'start'`

- [ ] **Step 3: Rewrite the option parser and entry point**

Replace `bridge_server.py:921-968` (`_parse_options`) with:

```python
def _parse_options() -> argparse.Namespace:
    """Parses the command line. `port` is None unless a flag supplied one.

    Returning None rather than PORT is what lets bridge_config.json own the port
    when no flag was passed, while a flag still wins over the file.

    The pairing token is mandatory. The old opt-outs (--insecure-no-auth, the
    STARCOMPANION_INSECURE_NO_AUTH env var, and the INSECURE_NO_AUTH sentinel
    file) are still recognised so an existing setup gets a clear message instead
    of silently starting with auth it no longer has.
    """
    parser = argparse.ArgumentParser(description="StarCompanion Desktop Bridge")
    parser.add_argument(
        "--insecure-no-auth", "--no-auth", action="store_true",
        dest="insecure_no_auth",
        help=argparse.SUPPRESS,  # retired — always errors out
    )
    parser.add_argument(
        "--console", action="store_true",
        help="run in the terminal with the startup banner instead of the window",
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help=f"port to listen on (default {PORT}, or the saved setting)",
    )
    parser.add_argument(
        "--log", type=str, default=None, dest="log_path",
        help="path to Game.log, for installs outside the default location "
             "(default: auto-detected)",
    )
    # Ignore unrecognised args rather than exiting — a stray argument from a
    # shortcut or file association shouldn't stop the bridge from starting.
    args, unknown = parser.parse_known_args()
    if unknown:
        print(f"[warn] ignoring unrecognised argument(s): {' '.join(unknown)}")

    global _log_path_override
    if args.log_path is not None:
        candidate = Path(args.log_path)
        if not candidate.exists():
            _refuse_bad_log_path(candidate)
        _log_path_override = candidate

    # Loudly reject the retired opt-outs rather than ignoring them: someone
    # using one believes the bridge is unauthenticated, and silently starting
    # with a token would look like the bridge is broken.
    sentinel = _app_dir() / "INSECURE_NO_AUTH"
    if args.insecure_no_auth:
        _refuse_no_auth("--insecure-no-auth is no longer supported.")
    if os.environ.get("STARCOMPANION_INSECURE_NO_AUTH", "").strip() not in ("", "0"):
        _refuse_no_auth("STARCOMPANION_INSECURE_NO_AUTH is no longer supported.")
    if sentinel.exists():
        _refuse_no_auth(f"The INSECURE_NO_AUTH file is no longer supported.\nDelete it: {sentinel}")

    return args
```

Replace `bridge_server.py:992` to end of file with:

```python
def run_console(port: int) -> None:
    """Today's terminal experience, unchanged."""
    configure_console_logging()
    try:
        asyncio.run(main(port=port))
    except KeyboardInterrupt:
        print("\nBridge stopped.")
    except OSError as e:
        if fatal_kind_for(e) == "PORT_IN_USE":
            print(f"\nERROR: port {port} is already in use — is the bridge already running?")
        else:
            print(f"\nERROR: {e}")
        with contextlib.suppress(EOFError):
            input("\nPress Enter to close...")
    except Exception as e:
        print(f"\nERROR: {e}")
        with contextlib.suppress(EOFError):
            input("\nPress Enter to close...")


def start() -> None:
    args = _parse_options()
    app_dir = _app_dir()

    # Imported here, not at module scope: console mode must keep working even if
    # core/ and ui/ are missing, and a --windowed build should not pay for Tk
    # until it needs it.
    from core.config import apply_cli, load

    cfg, warning = load(app_dir)
    cfg = apply_cli(cfg, cli_port=args.port, cli_log=args.log_path)

    if args.console:
        if warning:
            print(f"[warn] {warning}")
        run_console(cfg.port)
        return

    from ui.app import run_gui

    run_gui(cfg, app_dir, config_warning=warning)


if __name__ == "__main__":
    start()
```

Note `set_telemetry_enabled(cfg.telemetry_enabled)` is not called here — `BridgeApp.start_server` does it, and console mode intentionally ignores the GUI's master switch so `--console` behaves exactly as it does today.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_entrypoint.py -v`
Expected: 12 passed

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest tests/ -v`
Expected: everything passes, including the four pre-existing files.

- [ ] **Step 6: Verify console mode by hand**

Run: `python bridge_server.py --console`
Expected: the banner, the terminal QR, and `Waiting for app to connect...` exactly as before. Ctrl-C prints `Bridge stopped.`

- [ ] **Step 7: Commit**

```bash
git add bridge_server.py tests/test_entrypoint.py
git commit -m "feat: launch the window by default, keeping --console for the terminal

--port now defaults to None so bridge_config.json can own the port while a
flag still wins over it. core/ and ui/ are imported inside start() so console
mode does not depend on the GUI at all."
```

---

### Task 12: Package the windowed exe and document it

**Files:**
- Modify: `build_exe.bat`
- Modify: `README.md`
- Modify: `project.md`

**Interfaces:**
- Consumes: `assets/bridge.ico` from Task 6, `bridge_server.start` from Task 11.
- Produces: no code interfaces.

- [ ] **Step 1: Switch the build to a windowed exe**

Replace the PyInstaller invocation in `build_exe.bat` (lines 22-26) with:

```bat
python -m PyInstaller --onefile --windowed --clean --noconfirm ^
    --name StarCompanionBridge ^
    --icon assets\bridge.ico ^
    --add-data "assets\bridge.ico;assets" ^
    --hidden-import=pynput.keyboard._win32 ^
    --hidden-import=pynput.mouse._win32 ^
    bridge_server.py
```

- [ ] **Step 2: Regenerate the icon as part of the build**

Insert after the pip install block in `build_exe.bat` (after line 20):

```bat
if not exist "assets\bridge.ico" (
    python tools\make_icon.py
    if errorlevel 1 (
        echo ERROR: could not generate assets\bridge.ico
        pause
        exit /b 1
    )
)
```

- [ ] **Step 3: Document the window in the README**

Insert a new section immediately before `## Running the script directly`:

```markdown
---

## The bridge window

Launching the bridge opens a window, not a console. It shows:

- **Status** — a coloured dot and a line of text: starting, listening, connected
  (with the number of paired apps), or a failure and its reason.
- **Pairing** — the QR code plus the IP, port, token, and certificate
  fingerprint, each with a Copy button. The token is masked until you press
  **Show**, so a screenshot or a stream does not leak it, and the QR has its own
  **Hide QR** button for the same reason.
- **Multiple IP addresses** — if your PC has several (VPN, Hyper-V, or WSL
  adapters), the IP row becomes a dropdown. Picking a different one regenerates
  the QR, so you can try each without retyping anything in the app.
- **Telemetry** — whether `Game.log` was found, and where.
- **Activity** — the live log, with Pause and Clear. The full history is always
  written to `bridge.log` next to the exe, which is the file to attach to a bug
  report.

If the bridge cannot start — most often because the port is already in use — the
window stays open, says so, and offers **Retry** and **Open Settings**. Nothing
disappears before you can read it.

### Settings

The gear button opens Settings:

| Setting | Notes |
|---|---|
| **Port** | 1024–65535. Checked for availability before saving, so you cannot save a port that is already taken. Changing it restarts the listener. |
| **Game.log** | Leave empty to auto-detect; the panel shows what auto-detection found. **Browse…** picks the file for a non-standard install. |
| **Live telemetry** | Turns the whole telemetry feature off. With it off the bridge behaves as though no `Game.log` exists. |
| **Start with Windows** | Adds a per-user entry to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. No admin rights needed. Available only for the packaged exe. |

Settings are saved to `bridge_config.json` next to the exe. A command-line flag
always wins over the file: if you pass `--port` or `--log`, that field appears
greyed out with a note saying which flag controls it.

### Running in the terminal instead

```
python bridge_server.py --console
```

That is the original behaviour: startup banner, QR code drawn in the terminal,
and log lines on stdout. Use it for headless setups and for debugging.
```

- [ ] **Step 4: Update the options list in the README**

In `## Running the script directly`, replace the options block with:

```
--console            run in the terminal with the startup banner, instead of
                      opening the window
--port N             listen on a different port (default 8765, or the port saved
                      in bridge_config.json)
--log PATH           read Game.log from PATH instead of auto-detecting it
                      (see "Live telemetry" below)
```

- [ ] **Step 5: Update the secrets note in the README**

In the **Secrets** paragraph under "Pairing / security", after the existing
sentence, add:

```markdown
`bridge_config.json` and `bridge.log` are also gitignored — they are per-machine
and can contain your install paths.
```

- [ ] **Step 6: Move the GUI work into Done in project.md**

Move the **Desktop GUI** entry from `## Now` to `## Done`, rewritten as:

```markdown
- Desktop GUI: dark-themed Tkinter window with status, pairing card and QR,
  telemetry state, live activity pane, and a settings panel (port, Game.log
  path, telemetry switch, start-with-Windows). Failures stay on screen with a
  Retry rather than closing the window. `--console` keeps the original terminal
  behaviour. Design:
  `docs/superpowers/specs/2026-08-14-bridge-gui-design.md`.
```

Leave `## Now` holding whatever comes next, and keep the two GUI follow-ups
(real icon artwork, tray icon) under `## Next up`.

- [ ] **Step 7: Verify the whole suite one last time**

Run: `python -m pytest tests/ -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add build_exe.bat README.md project.md
git commit -m "build: package a windowed exe, and document the window

--windowed means no console flashes behind the window, which is the whole
point of the change. The README gains a window section and keeps its console
instructions, which --console leaves accurate."
```

- [ ] **Step 9: Windows smoke test (manual, requires the target machine)**

1. Copy the repo to the Windows PC and run `build_exe.bat`.
2. Run `dist\StarCompanionBridge.exe`. Confirm: no console window appears; the
   window opens dark-themed with a sharp (not blurry) font; the QR renders; the
   status dot is blue and reads "Listening — waiting for app".
3. Pair the phone by scanning the QR. Confirm the dot turns green and reads
   "Connected — 1 app", and that a keystroke shows in the activity pane.
4. Start a second copy of the exe. Confirm the second window opens, shows a red
   dot with "Port 8765 in use — is the bridge already running?", and offers
   Retry and Open Settings — and that it does not vanish.
5. Open Settings, change the port to 8766, Save & Restart. Confirm the card
   repopulates and the phone reconnects after re-pairing to the new port.
6. Tick **Start with Windows**, save, and confirm the value exists:
   `reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v StarCompanionBridge`
7. Screenshot the window and send it back for layout fixes.
```

---

## Notes for the implementer

- **Never edit the four pre-existing test files.** If a refactor breaks one, the
  refactor is wrong.
- **Nothing in `ServerThread` or anything it calls may touch a widget.** The only
  legal outward call is `bus.emit`.
- `bridge_server.py` must not import `core` or `ui` at module scope — Task 11
  imports them inside `start()` on purpose, so `--console` keeps working with
  those packages absent and a Tk-less environment can still run the server.
- Two snippets deliberately use inline `__import__(...)` to stay
  self-contained in this document (`core/events.py`, `ui/app.py`,
  `ui/settings.py`). Each step says so; convert them to top-level imports.
