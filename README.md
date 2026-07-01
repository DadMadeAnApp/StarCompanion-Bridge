# StarCompanion Desktop Bridge

Receives commands from the StarCompanion iOS app over your local network and injects keystrokes into Star Citizen.

---

## Running the script directly

Requires Python 3.10 or later — download from [python.org](https://www.python.org/downloads/).

```
python bridge_server.py
```

Dependencies (`websockets`, `pynput`) install automatically on first launch.

---

## Building a standalone .exe

Do this once on your Windows PC. The result is a single `bridge_server.exe` that runs on any Windows machine with no Python required.

### 1. Install PyInstaller

```
pip install pyinstaller
```

### 2. Build

Run this from the `Bridge` folder:

```
pyinstaller --onefile --console bridge_server.py
```

`--onefile` bundles everything into a single executable.  
`--console` keeps the terminal window open so you can see the IP address and log output.

### 3. Find the exe

PyInstaller creates a `dist` folder in the same directory. Your executable is at:

```
Bridge\dist\bridge_server.exe
```

You can move `bridge_server.exe` anywhere — it has no external dependencies.

### 4. Windows Firewall

The first time you run the exe, Windows may show a firewall prompt. Click **Allow access** to let the app connect over your local network. If you miss the prompt, add a rule manually:

> Windows Defender Firewall → Advanced Settings → Inbound Rules → New Rule  
> Type: Port → TCP → Port 8765 → Allow the connection

---

## Usage

1. Run `bridge_server.exe` (or the Python script)
2. The window shows your local IP address — enter it in the StarCompanion app
3. Make sure **Star Citizen is the active/focused window** before tapping controls
4. Tap **Connect** in the app → Flight tab

---

## Keybind customization

Keybinds are set in the **StarCompanion app** under Flight → Edit Keybinds. No changes to the bridge are needed. The app sends the key with every command, so the bridge just injects whatever it receives.

Supported key names: single characters (`a`–`z`, `0`–`9`, `\`, etc.) or named specials:

```
F1–F12   CAPS   ESC   TAB   SPACE   ENTER   BACKSPACE
DELETE   HOME   END   PAGEUP   PAGEDOWN
UP   DOWN   LEFT   RIGHT
```
