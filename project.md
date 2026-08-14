# StarCompanion Bridge — project tracking

## Now

**Desktop GUI** — replacing the console window with a dark-themed Tkinter window
(status, pairing QR, activity log, settings panel). Console mode survives behind
`--console`.

Design: [`docs/superpowers/specs/2026-08-14-bridge-gui-design.md`](docs/superpowers/specs/2026-08-14-bridge-gui-design.md)
Status: design approved 2026-08-14, awaiting spec review, then implementation plan.

## Next up

- Real app icon artwork, replacing the generated placeholder `.ico`.
- System tray icon / minimize-to-tray (deliberately cut from the GUI work).
- Windows visual QA pass on the shipped window (screenshot loop with a real PC).

## Done

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
