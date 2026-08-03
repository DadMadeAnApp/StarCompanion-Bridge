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

## Running the script directly

Requires Python 3.10 or later — download from [python.org](https://www.python.org/downloads/).

```
python bridge_server.py
```

Dependencies (`websockets`, `pynput`, `cryptography`, `qrcode`) install automatically on first launch.

Options:

```
--port N             listen on a different port (default 8765)
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
