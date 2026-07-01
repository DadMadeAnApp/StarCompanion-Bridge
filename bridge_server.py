#!/usr/bin/env python3
"""
StarCompanion Desktop Bridge
Double-click to run. No setup required beyond Python 3.10+.
Dependencies install automatically on first launch.

Keybinds are configured in the StarCompanion app — no editing needed here.
"""

# ── Bootstrap: install missing deps, then re-exec so they're importable ───────
import subprocess, sys, os

_DEPS = ["websockets>=12.0", "pynput>=1.7", "cryptography>=41.0"]

def _bootstrap():
    missing = [p for p in _DEPS if _import_ok(p.split(">=")[0])]
    if not missing:
        return
    print(f"[setup] Installing {', '.join(missing)} ...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", *missing]
    )
    os.execv(sys.executable, [sys.executable] + sys.argv)

def _import_ok(name: str) -> bool:
    try:
        __import__(name)
        return False
    except ImportError:
        return True

if not getattr(sys, "frozen", False):
    _bootstrap()

# ── Imports ────────────────────────────────────────────────────────────────────
import asyncio
import datetime
import json
import logging
import socket
import ssl
from pathlib import Path
import websockets
from pynput.keyboard import Key, KeyCode, Controller
from pynput.mouse import Button, Controller as MouseController

# ── Config ─────────────────────────────────────────────────────────────────────
PORT = 8765

# Named special keys the app may send instead of a single character.
# Covers all keys Star Citizen is likely to use.
SPECIAL_KEYS: dict[str, Key] = {
    "CAPS": Key.caps_lock, "CAPSLOCK": Key.caps_lock,
    "F1":  Key.f1,  "F2":  Key.f2,  "F3":  Key.f3,  "F4":  Key.f4,
    "F5":  Key.f5,  "F6":  Key.f6,  "F7":  Key.f7,  "F8":  Key.f8,
    "F9":  Key.f9,  "F10": Key.f10, "F11": Key.f11, "F12": Key.f12,
    "ESC": Key.esc, "ESCAPE": Key.esc,
    "TAB": Key.tab,
    "SPACE": Key.space,
    "ENTER": Key.enter, "RETURN": Key.enter,
    "BACKSPACE": Key.backspace,
    "DELETE": Key.delete,
    "INSERT": Key.insert,
    "HOME": Key.home,
    "END": Key.end,
    "PAGEUP": Key.page_up, "PGUP": Key.page_up,
    "PAGEDOWN": Key.page_down, "PGDN": Key.page_down,
    "UP": Key.up, "DOWN": Key.down, "LEFT": Key.left, "RIGHT": Key.right,
    "NUM_LOCK": Key.num_lock,
    "SCROLL_LOCK": Key.scroll_lock,
    "PAUSE": Key.pause,
    "PRINT_SCREEN": Key.print_screen,
}

# Modifier keys — used when the keybind is "MOD+KEY" (e.g. "ALT+C").
MODIFIER_KEYS: dict[str, Key] = {
    "ALT":    Key.alt_l,  "LALT":   Key.alt_l,  "RALT":  Key.alt_r,
    "CTRL":   Key.ctrl_l, "LCTRL":  Key.ctrl_l, "RCTRL": Key.ctrl_r,
    "SHIFT":  Key.shift,  "LSHIFT": Key.shift,
    "WIN":    Key.cmd,    "META":   Key.cmd,
}

# ── Logging ─────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

# ── Helpers ─────────────────────────────────────────────────────────────────────
_keyboard = Controller()
_mouse    = MouseController()

def _local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"

def _generate_cert(cert_path: Path, key_path: Path) -> None:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    print("[setup] Generating TLS certificate (first launch)…")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "StarCompanion Bridge")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"[setup] Certificate saved to {cert_path}")


def _ensure_ssl_context() -> ssl.SSLContext:
    # When frozen by PyInstaller, __file__ is a temp dir — use the exe's dir instead.
    script_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    cert_path  = script_dir / "bridge_cert.pem"
    key_path   = script_dir / "bridge_key.pem"
    if not (cert_path.exists() and key_path.exists()):
        _generate_cert(cert_path, key_path)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    return ctx


def _resolve(key_str: str):
    """Return (key, mods) or (None, []) on unknown key."""
    parts = key_str.upper().split("+")
    key_name = parts[-1]
    mods = [MODIFIER_KEYS[p] for p in parts[:-1] if p in MODIFIER_KEYS]
    if key_name in SPECIAL_KEYS:
        return SPECIAL_KEYS[key_name], mods
    if len(key_name) == 1:
        # ponytail: VK code path — KEYEVENTF_UNICODE is invisible to DirectInput/Raw Input (Star Citizen)
        return KeyCode(vk=ord(key_name)), mods
    logging.warning(f"Unknown key: {key_str!r}")
    return None, []

def press(key_str: str) -> None:
    key, mods = _resolve(key_str)
    if key is None:
        return
    for mod in mods:
        _keyboard.press(mod)
    _keyboard.press(key)
    _keyboard.release(key)
    for mod in reversed(mods):
        _keyboard.release(mod)

async def press_hold(key_str: str, duration: float) -> None:
    key, mods = _resolve(key_str)
    if key is None:
        return
    for mod in mods:
        _keyboard.press(mod)
    _keyboard.press(key)
    await asyncio.sleep(duration)
    _keyboard.release(key)
    for mod in reversed(mods):
        _keyboard.release(mod)

# ── WebSocket handler ─────────────────────────────────────────────────────────
async def handle(ws: websockets.ServerConnection) -> None:
    logging.info(f"App connected  {ws.remote_address}")
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logging.warning(f"Bad JSON: {raw!r}")
                continue

            kind = msg.get("type")

            if kind == "command":
                action = msg.get("action", "")
                key_str = msg.get("key", "")
                hold_secs = msg.get("hold")
                if key_str:
                    if hold_secs:
                        await press_hold(key_str, float(hold_secs))
                        logging.info(f"HOLD {action!r}  →  {key_str!r}  ({hold_secs}s)")
                    else:
                        press(key_str)
                        logging.info(f"KEY  {action!r}  →  {key_str!r}")
                else:
                    logging.warning(f"No key provided for action: {action!r}")

            elif kind == "mouse":
                action = msg.get("action", "")
                if action == "click":
                    _mouse.click(Button.left)
                    logging.info("MOUSE click (laser toggle)")
                elif action == "alt_click":
                    _keyboard.press(Key.alt_l)
                    _mouse.click(Button.left)
                    _keyboard.release(Key.alt_l)
                    logging.info("MOUSE alt+click (switch laser)")
                elif action == "scroll_up":
                    _mouse.scroll(0, 1)
                    logging.info("MOUSE scroll up (power +)")
                elif action == "scroll_down":
                    _mouse.scroll(0, -1)
                    logging.info("MOUSE scroll down (power −)")
                else:
                    logging.warning(f"Unknown mouse action: {action!r}")

            else:
                logging.warning(f"Unknown message type: {kind!r}")

    except websockets.exceptions.ConnectionClosed:
        pass
    logging.info(f"App disconnected  {ws.remote_address}")


# ── Entry point ───────────────────────────────────────────────────────────────
async def main() -> None:
    ip      = _local_ip()
    ssl_ctx = _ensure_ssl_context()
    print()
    print("=" * 48)
    print("  StarCompanion Desktop Bridge  (WSS/TLS)")
    print(f"  Enter this IP in the app:  {ip}")
    print(f"  Port: {PORT}")
    print("=" * 48)
    print()
    logging.info("Waiting for app to connect... (Star Citizen must be the active window)")
    async with websockets.serve(handle, "0.0.0.0", PORT, ssl=ssl_ctx):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBridge stopped.")
    except Exception as e:
        print(f"\nERROR: {e}")
        input("\nPress Enter to close...")
