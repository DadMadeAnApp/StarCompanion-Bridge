# StarCompanion Bridge — project tracking

## Now

Nothing in progress.

## Next up

- Verify on the Windows PC with a real install (branch `feat/p4k-resolver-keybinds`): p4k read
  (encrypted-entry flag, entry names; if `global.ini`/`defaultProfile.xml` are encrypted, names and
  default keybinds are simply unavailable), location-name key variants in `core/localization.py`,
  `defaultProfile.xml` shape and SC key-name spellings in `core/keybinds.py`.

- Real app icon artwork, replacing the generated placeholder `.ico`.
- System tray icon / minimize-to-tray (deliberately cut from the GUI work).

## Done

- Decision 2026-11-17 (owner): encrypted p4k entries are deliberately not read; the p4k reader has
  no decryption code and raises `P4KError("entry is encrypted; not supported")` for them.

- Built, unverified against a real install: p4k reader, name resolution (`locationName`,
  notification `@key`), keybind export (`{"type":"keybinds"}`). Tests use synthetic fixtures only.

- WSS/TLS websocket server with mandatory pairing token, QR pairing, cert pinning.
- Keyboard injection: letters, digits, punctuation via keyboard layout, named
  special keys, numpad by virtual key, `MOD+KEY` combinations, held keys.
- Mouse injection: click, alt-click, held click, scroll.
- Connection handling: 4-connection cap with oldest-dropped, 100 msg/s rate limit,
  4096-byte message cap, opt-in acks, keepalive pings that never disconnect a
  backgrounded phone.
- Live telemetry: `Game.log` tailer with three feeds (notifications, location
  context, objectives), per-connection opt-in, account id and character GEID
  stripped before any frame leaves the machine.
- Game install detection outside `C:\Program Files`: default paths, then the RSI
  launcher log, then conventional folder names on every drive.
- Verified against a running game 2026-08-13 (Build 12344265 / SC 4.9.188).
  Objectives are unreliable because the game logs them inconsistently; the app
  shows notifications instead.
- Standalone single-file exe via `build_exe.bat` / PyInstaller.
- Test suite under `tests/`.
- Desktop GUI: dark-themed Tkinter window with status, pairing card and QR,
  telemetry state, live activity pane, and a settings panel (port, Game.log
  path, telemetry switch, start-with-Windows). Failures stay on screen with a
  Retry rather than closing the window. `--console` keeps the original terminal
  behaviour. Design:
  `docs/superpowers/specs/2026-08-14-bridge-gui-design.md`.
- Windowed exe verified building and running on a real Windows PC (2026-08-15).
  The first build there failed at startup with `No module named 'telemetry'`;
  the local packages are now named explicitly in `build_exe.bat`, which also
  refuses to build from an incomplete checkout. The root cause on that machine
  was never reproduced — a clean build collects all three packages unaided.
