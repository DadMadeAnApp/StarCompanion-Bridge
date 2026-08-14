# Desktop GUI for StarCompanion Bridge

**Date:** 2026-08-14
**Status:** approved design, not yet implemented

## Problem

The bridge presents itself as a console window. An end user double-clicks
`StarCompanionBridge.exe` and gets a black box printing a banner, an ANSI QR code,
and log lines. Three consequences:

1. It reads as a script, not a product.
2. A startup failure is invisible. The current code prints the error and waits on
   `input()`; the window closes when the user presses a key, and the reason is gone.
3. Every setting is a command-line flag, which end users will not use.

## Goal

A single dark-themed native window that shows connection status, pairing details,
and live activity, with a settings panel replacing the flags. The console mode
survives behind `--console` for debugging and headless use.

## Decisions

| Question | Decision |
|---|---|
| UI toolkit | Tkinter / ttk. Ships with Python, no new dependency, PyInstaller-safe. |
| Scope | Status, pairing, activity log, **and** a full settings panel. |
| Default mode | GUI. `--console` opts back into today's behavior. |
| Polish | Hand-styled dark theme, custom palette and fonts, app icon. Stock widgets. |
| Concurrency | asyncio server on a daemon worker thread, Tk `mainloop()` on the main thread. |
| Icon | Generated placeholder `.ico` for now; real art later. |
| Verification | Headless logic tests run locally; Windows visual QA by screenshot loop. |

Rejected: driving Tk from the asyncio loop via `root.update()` (a modal file dialog
would block the server, and the settings panel needs one); a two-process GUI+server
split (IPC protocol, doubled packaging, restart-per-setting, for crash isolation we
do not need).

## Architecture

```
bridge_server.py     entry point: parse args -> console path or GUI path
core/config.py       NEW  bridge_config.json load/save/defaults, precedence
core/events.py       NEW  BridgeEvent dataclasses + thread-safe EventBus
ui/app.py            NEW  Tk root, layout, root.after drain loop, lifecycle
ui/theme.py          NEW  palette, fonts, ttk style setup
ui/widgets.py        NEW  StatusDot, CopyRow, ActivityPane, QrImage
ui/settings.py       NEW  settings Toplevel + validation
ui/log_handler.py    NEW  logging.Handler -> EventBus
telemetry/*          unchanged
```

### Control flow

1. `bridge_server.py` parses args. `--console` runs today's path verbatim: banner,
   terminal QR, stdout logging. Otherwise the GUI path.
2. GUI path builds the Tk root on the main thread, installs `GuiLogHandler` on the
   root logger, and starts `ServerThread` (daemon) running `asyncio.run(serve(...))`.
3. The server thread only ever calls `bus.emit(event)`. It never touches Tk.
4. `root.after(100, drain)` pulls events and updates widgets.
5. GUI to server goes through `loop.call_soon_threadsafe`, used only by Restart and
   apply-settings.

### Refactor of existing code

Surgical, and deliberately behavior-preserving:

- `main()` splits into `serve(port, on_ready)` — bind and run, no printing — and
  `_console_banner(...)`, which holds every current `print`. Console path is
  `serve` plus banner. GUI path is `serve` plus an `on_ready` that emits `Ready`.
- `logging.basicConfig` moves behind a function. Under a `--windowed` exe
  `sys.stdout` is `None`, so today's `StreamHandler(sys.stdout)` would raise on the
  first record. The GUI path installs `GuiLogHandler` plus a rotating file handler
  writing `bridge.log`, and no StreamHandler.
- The dependency auto-installer at the top of the file prints and shells out to pip.
  Under `--windowed` those prints go nowhere. The exe ships its dependencies bundled,
  so that path is script-only and stays as-is.

## Window

Fixed width 520px, min height 620, resizable vertically only so the activity pane
grows. Four stacked regions.

```
+- StarCompanion Bridge ------------------------- - [] x +
|  * Listening - waiting for app             (gear) (restart)
+--------------------------------------------------------+
|   +------------+   IP    192.168.1.42    [copy]        |
|   |   QR       |   Port  8765            [copy]        |
|   |            |   Token a1b2...f9       [copy] [eye]  |
|   +------------+   Cert  A1:B2:...       [copy]        |
|          Scan in the StarCompanion app to pair         |
+--------------------------------------------------------+
|  Telemetry  * Game.log found - 2 feeds active          |
+--------------------------------------------------------+
|  Activity                        [ ] Pause    [Clear]  |
|  21:04:11  App connected  192.168.1.7                  |
|  21:04:12  KEY  quantum_travel -> b                    |
+--------------------------------------------------------+
```

Theme: background `#14181d`, accent `#4aa8ff`, warning amber, error red. Segoe UI 10
for chrome, Consolas 9 for the activity pane. `SetProcessDpiAwareness(1)` at startup,
guarded, or text is blurry on any scaled display.

### Status states

One enum is the single source of truth for the dot color and the header text.

| State | Dot | Header text |
|---|---|---|
| `STARTING` | grey | Starting... |
| `LISTENING` | blue | Listening - waiting for app |
| `CONNECTED` | green | Connected - N app(s) |
| `PORT_IN_USE` | red | Port N in use - is the bridge already running? |
| `ERROR` | red | Error - see activity log |

### Details

- **Multiple local IPs** turn the IP row into a read-only combobox listing all of
  them, first selected. Changing the selection regenerates the QR. This replaces the
  README's "try the first one" advice with something the user can act on.
- **Activity pane** keeps a 500-line ring buffer in memory and auto-scrolls unless
  the user has scrolled up or ticked Pause. Full history always goes to `bridge.log`.
  Warnings render amber, errors red.
- **Token is masked** by default so a screenshot or stream does not leak it; an eye
  toggle reveals it. The QR leaks it too and gets its own Hide toggle, defaulting to
  visible because pairing is the point of first run.

## Settings and config

`bridge_config.json` lives beside the exe, via the existing `_app_dir()`. Gitignored:
it holds no secrets but is per-machine.

```json
{
  "version": 1,
  "port": 8765,
  "log_path": null,
  "telemetry_enabled": true,
  "start_with_windows": false,
  "minimize_to_tray": false,
  "window": {"height": 620}
}
```

**Precedence: CLI flag > config file > auto-detect.** When `--port` or `--log` is
passed, that field renders disabled with the hint "set by --port on the command
line", so the panel never misreports what is in effect. Unknown keys are preserved
across saves. A corrupt file is renamed to `bridge_config.json.bad`, defaults are
used, and a warning goes to the activity log — never a hard startup failure.

`minimize_to_tray` is reserved in the schema and not surfaced in the panel. A tray
icon is out of scope for this work.

### Panel

A modal `Toplevel` in the same theme, four rows plus Cancel and Save.

| Setting | Widget | Applies |
|---|---|---|
| Port | spinbox, 1024-65535 | needs restart; Save offers **Save & Restart** |
| Game.log path | entry + Browse... + Auto-detect, shows the resolved path when auto | live, the tailer re-targets |
| Live telemetry | checkbox | live; a master switch above the existing per-connection opt-in |
| Start with Windows | checkbox | immediate |

Validation runs before Save. The port must be free — test-bind and release — or the
field goes red with "port in use" rather than restarting into a dead state. A chosen
log path must exist and be readable, reusing the existing `--log` validation without
its `sys.exit`.

**Start with Windows** writes a per-user autostart entry:
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `StarCompanionBridge`,
pointing at the current exe path. User-scoped, no admin required; unticking deletes
the value. Two behaviors are implemented rather than hidden: the checkbox is disabled
with a tooltip when running as a `.py` script, because there is no stable exe path to
register; and if the exe is later moved, the stale entry is detected at startup and
the checkbox self-corrects.

## Events

Frozen dataclasses in `core/events.py`. This is the complete set the server thread
may produce.

| Event | Fields | Effect |
|---|---|---|
| `Ready` | ips, port, token, cert_fp, pairing_uri | fill pair card, render QR, dot to LISTENING |
| `ClientCount` | n | dot to CONNECTED or LISTENING, header text |
| `TelemetryState` | found, path, feeds | telemetry strip |
| `LogLine` | ts, level, text | append to activity pane |
| `Fatal` | kind, message | dot red, message row, server thread is dead |

`EventBus` wraps `queue.Queue(maxsize=2000)`. `emit()` never blocks: on a full queue
it drops the event and bumps a counter, and the drain surfaces "... N events dropped"
once per tick rather than losing lines silently. The drain handles at most 200 events
per 100ms tick, so a 100 msg/s flood cannot freeze the window.

## Failure handling

This is the substantive win over the console.

1. `serve()` binds inside a try. An `OSError` carrying an address-in-use code
   (`10048`, `48`, `98`, or the `winerror` variant) becomes `Fatal(PORT_IN_USE)`.
2. Any other exception in the server thread becomes `Fatal(CRASH, str(e))`, with the
   full traceback written to `bridge.log`.
3. On `Fatal` the UI shows a red dot, a message row with the reason, and a **Retry**
   button. The window stays open. Nothing vanishes.
4. `PORT_IN_USE` additionally offers **Open Settings**, because changing the port is
   the fix.

## Lifecycle

On window close:

1. `loop.call_soon_threadsafe(stop_event.set)`
2. `serve()` unwinds, closing the websockets server and stopping the tailer
3. the GUI joins the thread with a 3s timeout; the thread is a daemon, so a hung
   tailer cannot leave a zombie process behind
4. `root.destroy()`

Restart — the header button, and Save & Restart — is steps 1 through 3 followed by a
fresh `ServerThread`, with the pair card cleared to `STARTING` in between. The token
and certificate persist, so a restart does not re-pair the user's phone.

## Testing

No new dependencies. The QR comes from the `qrcode` package already in use: take
`qr.get_matrix()` and build a `tk.PhotoImage` with `.put()` and hex row strings. No
Pillow, no pystray.

All tests below are headless and run on the macOS development machine.

| File | Covers |
|---|---|
| `tests/test_config.py` | defaults, save round-trip, unknown-key preservation, corrupt file to `.bad` plus defaults, CLI-over-config precedence |
| `tests/test_events.py` | drop-on-full plus counter, bounded drain, ordering |
| `tests/test_log_handler.py` | `logging.info` becomes a `LogLine`, level to color mapping, no crash when `sys.stdout is None` |
| `tests/test_status.py` | every event maps to the expected dot and text; `Fatal` latches until Retry |
| `tests/test_autostart.py` | registry path from exe path, script mode disabled, stale-entry detection, `winreg` mocked |
| `tests/test_serve_refactor.py` | `serve()` binds and calls `on_ready` with the right payload; port-in-use maps to the right `Fatal` kind |

The existing four test files must pass untouched. That is the guard that the server
core's behavior did not change. One Tk smoke test builds the window and drains a
scripted event sequence without raising, skipped when no display is available.

## Packaging

`build_exe.bat` changes: `--console` becomes `--windowed`, plus
`--icon=assets/bridge.ico` and an `--add-data` entry for the icon. The dependency
list is unchanged. `assets/bridge.ico` is generated programmatically for now — a flat
blue disc with "SC" — and replaced with real art later.

## Documentation

`--console` mode is unchanged, so the README's existing console instructions stay
accurate. The work adds a GUI section and a settings section; no rewrite.

## Out of scope

- System tray icon and minimize-to-tray.
- Any change to the wire protocol, pairing, or telemetry parsing.
- Real icon artwork.
- Rendering a mission-objectives list, which the game log cannot support.
