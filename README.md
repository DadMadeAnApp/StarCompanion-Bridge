# StarCompanion Desktop Bridge

Receives commands from the StarCompanion iOS and Android app over your local network and injects keystrokes into Star Citizen.

---

## Pairing / security

The bridge requires a pairing token to prevent anyone on your network from injecting keystrokes into your PC.

**First launch**: The bridge generates a token and saves it to `bridge_token.txt` next to the script or exe. The token is printed in the startup banner.

**Pairing by QR (easiest)**: Below the banner the bridge prints a QR code. Scan it in the StarCompanion app and it fills in the IP, port, token, and certificate fingerprint in one step — no typing. If the console can't draw the QR (output redirected to a file, or a terminal without ANSI support), the bridge prints the same `starcompanion://pair?…` link as text, which the app also accepts.

**Pairing by hand**: enter the IP address, port, and token from the banner in the app.

**Token delivery**: The app can supply the token in two ways:
- As an HTTP header: `Authorization: Bearer <token>`
- As a query string on the WebSocket URL: `?token=<token>`

If neither is present, the bridge waits up to 10 seconds for a first message of the form `{"type":"auth","token":"..."}`.

**Authentication flow**: On success, the server replies `{"type":"auth","ok":true}`. On failure, it replies `{"type":"auth","ok":false}` and closes the connection with code 4401.

**Auth cannot be disabled.** The token is always required. The three former
opt-outs — the `--insecure-no-auth` / `--no-auth` flag, the
`STARCOMPANION_INSECURE_NO_AUTH` environment variable, and the `INSECURE_NO_AUTH`
sentinel file — now refuse to start and print an error instead of quietly
launching an unauthenticated bridge. Delete the sentinel file if you have one.

They existed as a stopgap while the app lacked pairing support. With QR pairing
there is no longer a reason to run a bridge that lets anyone on the network
inject keystrokes into your PC.

**Secrets**: `bridge_token.txt`, `bridge_key.pem`, and `bridge_cert.pem` are gitignored — they are cryptographic material and should never be committed or shared.

`bridge_config.json` and `bridge.log` are also gitignored — they are per-machine
and can contain your install paths.

**Certificate pinning**: The startup banner prints your certificate's SHA-256 fingerprint. Clients that want to verify the server identity can pin this value.

---

## Message protocol

All communication is JSON. The bridge acknowledges every message with a reply.

**Keyboard command**:
```json
{
  "type": "command",
  "action": "<action name>",
  "key": "<key name>",
  "hold": <optional seconds, clamped to max 10>,
  "id": <optional request ID>
}
```

**Mouse action**:
```json
{
  "type": "mouse",
  "action": "click" | "alt_click" | "scroll_up" | "scroll_down",
  "button": "left" | "right" | "middle",
  "amount": <1–10, optional, scroll only>,
  "id": <optional request ID>
}
```

**Ping** (for keepalive):
```json
{
  "type": "ping"
}
```

**Acknowledgement** (reply to any message):
```json
{
  "type": "ack",
  "ok": true | false,
  "id": <echoed if present in request>,
  "error": "<error message if ok=false>"
}
```

**Acks are opt-in.** The server replies *only* to messages that include an `id`
field. Clients that don't send one receive no inbound frames at all, exactly as
with older bridge versions. Send an `id` on every message to get error reporting —
this is how a key name that isn't recognized surfaces as an error rather than
failing silently.

**Rate limiting**: Max 4 concurrent connections, 100 messages per second per connection, 4096 bytes per message.

**Idle connections**: The bridge sends a WebSocket ping every 20 seconds to keep
NAT and router mappings alive, but it will **never close a connection for failing
to answer one**. A phone that is backgrounded, throttled, or asleep stays
connected. When the 4-connection limit is reached, the oldest connection is
dropped to make room for the new one, so a reconnecting phone can never be locked
out by its own stale sockets.

---

## Live telemetry

The bridge can tail Star Citizen's `Game.log` and forward three feeds to the app: mission
objectives, HUD notifications, and location context.

**Verified against a running game on 2026-08-13** (Build 12344265 / SC 4.9.188): notifications
and location context match what a live session logs, and reach the app. **Mission objectives
are unreliable — through no fault of the bridge.** The game logs an objective's state and its
text on separate lines, and emits each of them only sometimes: across three missions in one
session, two accepted contracts produced one state record between them. The bridge forwards
what it can (it pairs the text from the HUD notification carrying a matching `ObjectiveId`,
which is the most consistent of the three sources), but a *complete* objective list cannot be
built from this log, and player-visible objectives are never observed reaching `COMPLETED`.
The app therefore shows notifications and does not render an objectives list.

**Opt-in per connection**: the bridge reads nothing and sends nothing until an app
subscribes. A connection that never sends a `subscribe` message gets no telemetry traffic at
all.

**Only the live log**: the bridge tails the current `Game.log` only. It never opens anything
under `logbackups\`.

**Read-only, local-only**: the bridge never writes to `Game.log`, and nothing it reads is
uploaded anywhere — the three feeds go only to the app connected over your local network.

**Privacy**: the account id and character GEID are stripped before a frame ever leaves the
machine. If you ever see either value in a frame, that's a bug — please report it.

**Non-default install path**: the bridge finds installs outside `C:\Program Files` on its own,
in three steps — the historical default paths, then the RSI launcher's own log
(`%APPDATA%\rsilauncher\logs\log.log`, which records where it installed the game), then
conventional folder names on every drive. Installs on `D:`/`E:`/`F:` are found without any
configuration. It is a bounded set of exact paths, never a recursive scan of your drives.

If it still can't find yours — an unusual folder name *and* no launcher log — point it at the
log directly:

```
python bridge_server.py --log "F:\Robert Space Industries\StarCitizen\LIVE\Game.log"
```

Note the folder may be **"Robert Space Industries"** or "Roberts" — real installs carry both
spellings, and auto-detection tries each. Passing `--log` with a path that doesn't exist fails
immediately at startup with an error, rather than silently falling back to auto-detection.

When no log can be found, a `subscribe` is answered with `ok:false` / `"log not found"`, which
the app surfaces as its own distinct state rather than an empty list.

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
  adapters), the IP row becomes a dropdown. The displayed address and the QR
  follow whichever one you pick, which is useful for entering an address by
  hand or expressing a preference — the QR itself already carries every local
  IP, so the app tries them on its own even without picking one here.
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

---

## Running the script directly

Requires Python 3.10 or later — download from [python.org](https://www.python.org/downloads/).

```
python bridge_server.py
```

Dependencies (`websockets`, `pynput`, `cryptography`, `qrcode`) install automatically on first launch.

Options:

```
--console            run in the terminal with the startup banner, instead of
                      opening the window
--port N             listen on a different port (default 8765, or the port saved
                      in bridge_config.json)
--log PATH           read Game.log from PATH instead of auto-detecting it
                      (see "Live telemetry" below)
```

---

## Building a standalone .exe

Do this once on your Windows PC. The result is a single executable that runs on any Windows machine with no Python required.

### 1. Build

Double-click `build_exe.bat`, or run it from a terminal in this folder. It installs PyInstaller and the runtime dependencies, then builds the exe.

### 2. Find the exe

```
dist\StarCompanionBridge.exe
```

Move it anywhere you like — it has no external dependencies.

Build it with `build_exe.bat` rather than a bare `pyinstaller` invocation: the script passes `--hidden-import=pynput.keyboard._win32` and `--hidden-import=pynput.mouse._win32`, which PyInstaller cannot detect on its own. Without them the exe builds cleanly and then fails at runtime the moment it tries to inject a keystroke.

### 3. Windows Firewall

The first time you run the exe, Windows may show a firewall prompt. Click **Allow access** to let the app connect over your local network. If you miss the prompt, add a rule manually:

> Windows Defender Firewall → Advanced Settings → Inbound Rules → New Rule  
> Type: Port → TCP → Port 8765 → Allow the connection

---

## Usage

1. Run `StarCompanionBridge.exe` (or the Python script)
2. The startup banner shows one or more local IP addresses. If there are multiple (due to VPN, Hyper-V, or WSL adapters), try the first one. Enter it in the StarCompanion app along with the pairing token.
3. Make sure **Star Citizen is the active/focused window** before tapping controls
4. Tap **Connect** in the app → Flight tab

---

## Keybind customization

Keybinds are set in the **StarCompanion app** under Flight → Edit Keybinds. No changes to the bridge are needed. The app sends the key with every command, so the bridge just injects whatever it receives.

**Single characters**: Any letter (`a`–`z`), digit (`0`–`9`), or punctuation (`\`, `-`, `=`, `[`, `]`, `;`, `'`, `,`, `.`, `/`, `` ` ``). Punctuation is resolved through the active Windows keyboard layout, so it works correctly even on non-US keyboard layouts.

**Named special keys**:
```
F1–F12   CAPS   ESC   TAB   SPACE   ENTER   BACKSPACE
DELETE   INSERT   HOME   END   PAGEUP   PAGEDOWN
UP   DOWN   LEFT   RIGHT
NUM_LOCK   SCROLL_LOCK   PAUSE   PRINT_SCREEN
```

**Modifier combinations**: Use `MOD+KEY` notation (e.g. `ALT+C`). Supported modifiers:
```
ALT, LALT, RALT
CTRL, LCTRL, RCTRL
SHIFT, LSHIFT, RSHIFT
WIN, META
```
