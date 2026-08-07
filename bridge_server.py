#!/usr/bin/env python3
"""
StarCompanion Desktop Bridge
Double-click to run. No setup required beyond Python 3.10+.
Dependencies install automatically on first launch.

Keybinds are configured in the StarCompanion app — no editing needed here.

The bridge injects keystrokes into whatever window has focus, so it always
requires the app to authenticate with the pairing token printed at startup.
There is no way to turn that off. Scan the QR below the startup banner to
pair without typing the token by hand.
"""

# ── Bootstrap: install missing deps, then re-exec so they're importable ───────
import subprocess, sys, os

_DEPS = ["websockets>=12.0", "pynput>=1.7", "cryptography>=41.0", "qrcode>=7.4"]
_RETRY_FLAG = "STARCOMPANION_BRIDGE_BOOTSTRAPPED"

def _is_missing(module: str) -> bool:
    try:
        __import__(module)
        return False
    except ImportError:
        return True

def _bootstrap():
    missing = [p for p in _DEPS if _is_missing(p.split(">=")[0])]
    if not missing:
        return
    if os.environ.get(_RETRY_FLAG):
        # We already installed once and the imports still fail — stop rather
        # than re-exec forever (usually a user-site vs. system Python split).
        print(f"ERROR: still cannot import {', '.join(missing)} after installing.")
        print(f"Try manually:  {sys.executable} -m pip install {' '.join(_DEPS)}")
        input("\nPress Enter to close...")
        sys.exit(1)
    print(f"[setup] Installing {', '.join(missing)} ...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", *missing]
        )
    except subprocess.CalledProcessError as e:
        print(f"ERROR: pip install failed (exit {e.returncode}).")
        input("\nPress Enter to close...")
        sys.exit(1)
    os.environ[_RETRY_FLAG] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)

if not getattr(sys, "frozen", False):
    _bootstrap()

# ── Imports ────────────────────────────────────────────────────────────────────
import argparse
import asyncio
import base64
import contextlib
import datetime
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import ssl
import time
from pathlib import Path
import websockets
from pynput.keyboard import Key, KeyCode, Controller
from pynput.mouse import Button, Controller as MouseController

# ── Config ─────────────────────────────────────────────────────────────────────
PORT = 8765
MAX_HOLD_SECONDS = 10.0      # a stuck hold means a stuck key in-game
MAX_CONNECTIONS = 4

# WebSocket keepalive. The library default (ping every 20s, drop after 20s
# without a pong) disconnects an idle phone within ~40s: mobile clients get
# throttled or suspended in the background and answer late or not at all.
#
# Keep sending pings — they hold NAT/router mappings open, which is its own
# cause of idle drops — but never close a connection just for missing a pong.
# Stale sockets are no longer a resource problem because a new connection
# evicts the oldest one (see make_handler), so patience costs nothing here.
PING_INTERVAL = 20.0
PING_TIMEOUT = None
MAX_MESSAGES_PER_SECOND = 100   # far above any tap rate; only stops runaway clients
MAX_MESSAGE_BYTES = 4096

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
    "ALT":    Key.alt_l,   "LALT":   Key.alt_l,   "RALT":  Key.alt_r,
    "CTRL":   Key.ctrl_l,  "LCTRL":  Key.ctrl_l,  "RCTRL": Key.ctrl_r,
    "SHIFT":  Key.shift_l, "LSHIFT": Key.shift_l, "RSHIFT": Key.shift_r,
    "WIN":    Key.cmd,     "META":   Key.cmd,
}

# Windows virtual-key codes for US-layout punctuation. ord() is NOT a VK code
# for these — ord('\\') is 92 but VK_OEM_5 is 0xDC — so pressing them via ord()
# silently sends the wrong key. Used as a fallback when VkKeyScanW is
# unavailable (non-Windows) or fails.
_OEM_VK: dict[str, int] = {
    ";": 0xBA, ":": 0xBA,
    "=": 0xBB, "+": 0xBB,
    ",": 0xBC, "<": 0xBC,
    "-": 0xBD, "_": 0xBD,
    ".": 0xBE, ">": 0xBE,
    "/": 0xBF, "?": 0xBF,
    "`": 0xC0, "~": 0xC0,
    "[": 0xDB, "{": 0xDB,
    "\\": 0xDC, "|": 0xDC,
    "]": 0xDD, "}": 0xDD,
    "'": 0xDE, '"': 0xDE,
}

MOUSE_BUTTONS = {"left": Button.left, "right": Button.right, "middle": Button.middle}

# Numeric-keypad virtual-key codes. These occupy their own VK range distinct
# from the main keyboard row, and VkKeyScanW maps *characters* — it resolves
# "0" and "+" to the main row, never to the keypad — so there is no fallback
# path that can reach these keys. An explicit table is the only way in.
# Star Citizen's Advanced Camera Controls default bindings are RAlt+Numpad n,
# which is why the app needs to be able to send them at all.
_NUMPAD_VKS: dict[str, int] = {
    "NUM0": 0x60, "NUM1": 0x61, "NUM2": 0x62, "NUM3": 0x63, "NUM4": 0x64,
    "NUM5": 0x65, "NUM6": 0x66, "NUM7": 0x67, "NUM8": 0x68, "NUM9": 0x69,
    "NUMSTAR": 0x6A, "NUMPLUS": 0x6B, "NUMENTER": 0x0D,
    "NUMMINUS": 0x6D, "NUMPERIOD": 0x6E, "NUMSLASH": 0x6F,
}

# ── Logging ─────────────────────────────────────────────────────────────────────
# The Windows console defaults to cp1252, which raises on any non-ASCII text
# in a log line. Force UTF-8 so logging can never crash on a stray character.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

# ── Helpers ─────────────────────────────────────────────────────────────────────
_keyboard = Controller()
_mouse    = MouseController()

# Serialises input injection so two overlapping holds can't interleave their
# modifier presses and leave the game seeing ALT down with no ALT up.
_input_lock = asyncio.Lock()


def _app_dir() -> Path:
    # When frozen by PyInstaller, __file__ is a temp dir — use the exe's dir instead.
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent


def _local_ips() -> list[str]:
    """All plausible LAN IPv4 addresses, best guess first.

    A single 8.8.8.8-route lookup picks the wrong adapter on machines with a
    VPN, Hyper-V, WSL or Docker interface, so return every candidate and let
    the user choose.
    """
    candidates: list[str] = []

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            candidates.append(s.getsockname()[0])
        except OSError:
            pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.append(info[4][0])
    except socket.gaierror:
        pass

    seen: list[str] = []
    for ip in candidates:
        try:
            addr = ipaddress.IPv4Address(ip)
        except ipaddress.AddressValueError:
            continue
        if addr.is_loopback or addr.is_link_local or ip in seen:
            continue
        seen.append(ip)

    return seen or ["127.0.0.1"]


def _restrict_permissions(path: Path) -> None:
    """Make a secret file readable only by the current user."""
    try:
        path.chmod(0o600)
    except OSError:
        pass
    if os.name == "nt":
        user = os.environ.get("USERNAME")
        if not user:
            return
        with contextlib.suppress(Exception):
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                check=False, capture_output=True,
            )


# ── Pairing token ──────────────────────────────────────────────────────────────
def _ensure_token() -> str:
    """Load or create the shared secret the app must present to connect."""
    token_path = _app_dir() / "bridge_token.txt"
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(18)
    token_path.write_text(token, encoding="utf-8")
    _restrict_permissions(token_path)
    return token


def _token_from_request(ws) -> str | None:
    """Pull the token from the Authorization header or the ?token= query.

    Mobile WebSocket clients can't always set headers, so both are accepted.
    """
    auth = ws.request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()

    path = ws.request.path
    if "?" in path:
        from urllib.parse import parse_qs, urlparse
        values = parse_qs(urlparse(path).query).get("token")
        if values:
            return values[0]
    return None


# ── TLS ────────────────────────────────────────────────────────────────────────
def _generate_cert(cert_path: Path, key_path: Path, ips: list[str]) -> None:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    print("[setup] Generating TLS certificate…")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "StarCompanion Bridge")])
    now = datetime.datetime.now(datetime.timezone.utc)

    # Include the LAN addresses the app actually dials, so a client that
    # pins/validates this cert can match the address it connected to.
    sans: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    for ip in ips:
        with contextlib.suppress(ipaddress.AddressValueError):
            sans.append(x509.IPAddress(ipaddress.IPv4Address(ip)))

    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    _restrict_permissions(key_path)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"[setup] Certificate saved to {cert_path}")


def _cert_covers(cert_path: Path, ips: list[str]) -> bool:
    """True if the existing cert already lists every current LAN address."""
    from cryptography import x509
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        listed = {str(a) for a in san.get_values_for_type(x509.IPAddress)}
    except Exception:
        return False
    return all(ip in listed for ip in ips)


def _cert_fingerprint(cert_path: Path) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    return cert.fingerprint(hashes.SHA256()).hex(":").upper()


def _cert_fingerprint_b64(cert_path: Path) -> str:
    """Same SHA-256 as _cert_fingerprint, base64url-encoded.

    The colon-hex form is 95 characters; this is 43, which keeps the pairing
    QR two versions smaller and correspondingly easier to scan.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    digest = cert.fingerprint(hashes.SHA256())
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


# ── Pairing QR ─────────────────────────────────────────────────────────────────
PAIRING_URI_VERSION = "2"


def _pairing_uri(ips: list[str], port: int, token: str | None, fingerprint: str) -> str:
    """Everything the app needs to connect, as one scannable URI.

    Every candidate address is included: _local_ips() can return several on a
    machine with VPN/Hyper-V/WSL adapters and the bridge cannot tell which one
    the phone can reach, so the app races them rather than the user guessing.
    """
    params = [("v", PAIRING_URI_VERSION)]
    params += [("h", ip) for ip in ips]
    params.append(("p", str(port)))
    if token:
        params.append(("t", token))
    params.append(("fp", fingerprint))
    from urllib.parse import urlencode
    return "starcompanion://pair?" + urlencode(params)


def _enable_vt() -> bool:
    """Turn on ANSI escape handling in a Windows console. True if usable."""
    if os.name != "nt":
        return sys.stdout.isatty()
    if not sys.stdout.isatty():
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _print_qr(data: str) -> bool:
    """Render a QR to the console. False if it could not be drawn.

    Drawn as explicit black-on-white with ANSI colours rather than the
    terminal's own palette: readers need dark modules on a light background,
    and both cmd.exe and Windows Terminal default to a dark scheme, which
    would otherwise produce an inverted symbol that many readers reject.
    Two rows of modules share one text row via half-block characters, because
    a character cell is about twice as tall as it is wide.
    """
    try:
        import qrcode
    except ImportError:
        return False
    if not _enable_vt():
        return False

    try:
        # Level L: a screen is a clean scanning surface, and the lower
        # redundancy keeps the symbol small enough to fit an 80x30 console.
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        matrix = qr.get_matrix()
    except Exception:
        return False

    WHITE_BG, BLACK_FG, RESET = "\033[47m", "\033[30m", "\033[0m"
    for y in range(0, len(matrix), 2):
        upper = matrix[y]
        lower = matrix[y + 1] if y + 1 < len(matrix) else [False] * len(upper)
        cells = []
        for up, low in zip(upper, lower):
            # A True module is dark; the fg is black and the bg is white, so
            # the half-block covers whichever half needs to be dark.
            cells.append("█" if up and low else "▀" if up else "▄" if low else " ")
        print(f"{WHITE_BG}{BLACK_FG}{''.join(cells)}{RESET}")
    return True


def _ensure_ssl_context(ips: list[str]) -> tuple[ssl.SSLContext, Path]:
    d = _app_dir()
    cert_path, key_path = d / "bridge_cert.pem", d / "bridge_key.pem"
    if not (cert_path.exists() and key_path.exists()):
        _generate_cert(cert_path, key_path, ips)
    elif not _cert_covers(cert_path, ips):
        print("[setup] Local IP changed — reissuing certificate.")
        _generate_cert(cert_path, key_path, ips)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    return ctx, cert_path


# ── Key resolution ─────────────────────────────────────────────────────────────
def _vk_for_char(ch: str) -> int | None:
    """Virtual-key code for a single printable character.

    Letters and digits map straight to their ASCII value on Windows. For
    everything else we ask the OS for the active keyboard layout's mapping
    (so non-US layouts work), falling back to the US OEM table.
    """
    if ch.isalnum() and ch.isascii():
        return ord(ch.upper())

    if os.name == "nt":
        try:
            import ctypes
            res = ctypes.windll.user32.VkKeyScanW(ctypes.c_wchar(ch))
            if res != -1:
                return res & 0xFF
        except Exception:
            pass

    return _OEM_VK.get(ch)


def _resolve(key_str: str):
    """Return (key, mods) or (None, []) on unknown key."""
    parts = key_str.upper().split("+")
    # A trailing "+" means the key itself is "+" (e.g. "CTRL++").
    if parts[-1] == "" and len(parts) > 1:
        parts = parts[:-1] + ["+"]
    key_name = parts[-1]
    mods = [MODIFIER_KEYS[p] for p in parts[:-1] if p in MODIFIER_KEYS]

    # Numpad names must resolve before SPECIAL_KEYS or the char/VkKeyScanW
    # fallback below can ever see them — otherwise "NUMPLUS" would fall
    # through to the main-row "+" (the exact bug this table exists to avoid).
    if key_name in _NUMPAD_VKS:
        return KeyCode(vk=_NUMPAD_VKS[key_name]), mods
    if key_name in SPECIAL_KEYS:
        return SPECIAL_KEYS[key_name], mods
    if len(key_name) == 1:
        # VK code path — KEYEVENTF_UNICODE is invisible to DirectInput/Raw
        # Input, which is how Star Citizen reads the keyboard.
        vk = _vk_for_char(key_name)
        if vk is not None:
            return KeyCode(vk=vk), mods
    # Caller reports this to the app and to the log — don't double-log here.
    return None, []


async def press(key_str: str) -> bool:
    key, mods = _resolve(key_str)
    if key is None:
        return False
    async with _input_lock:
        held = []
        try:
            for mod in mods:
                _keyboard.press(mod)
                held.append(mod)
            _keyboard.press(key)
            _keyboard.release(key)
        finally:
            for mod in reversed(held):
                with contextlib.suppress(Exception):
                    _keyboard.release(mod)
    return True


async def press_hold(key_str: str, duration: float) -> bool:
    key, mods = _resolve(key_str)
    if key is None:
        return False
    async with _input_lock:
        held = []
        try:
            for mod in mods:
                _keyboard.press(mod)
                held.append(mod)
            _keyboard.press(key)
            held.append(key)
            await asyncio.sleep(duration)
        finally:
            # Runs on cancellation too — a dropped connection mid-hold must
            # never leave a key physically stuck down.
            for k in reversed(held):
                with contextlib.suppress(Exception):
                    _keyboard.release(k)
    return True


# ── Message dispatch ───────────────────────────────────────────────────────────
def _coerce_hold(value) -> float | None:
    """Validated, clamped hold duration. None means 'not a hold'."""
    try:
        secs = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"hold must be a number, got {value!r}")
    if secs <= 0:
        return None
    return min(secs, MAX_HOLD_SECONDS)


async def _dispatch(msg: dict) -> tuple[bool, str | None]:
    """Perform one command. Returns (ok, error)."""
    kind = msg.get("type")

    if kind == "command":
        action = msg.get("action", "")
        key_str = msg.get("key", "")
        if not isinstance(key_str, str) or not key_str:
            return False, f"no key provided for action {action!r}"

        hold_secs = _coerce_hold(msg.get("hold")) if msg.get("hold") is not None else None
        if hold_secs is not None:
            ok = await press_hold(key_str, hold_secs)
            if ok:
                logging.info(f"HOLD {action!r}  ->  {key_str!r}  ({hold_secs}s)")
        else:
            ok = await press(key_str)
            if ok:
                logging.info(f"KEY  {action!r}  ->  {key_str!r}")
        return (True, None) if ok else (False, f"unknown key {key_str!r} for action {action!r}")

    if kind == "mouse":
        action = msg.get("action", "")
        button = MOUSE_BUTTONS.get(str(msg.get("button", "left")).lower(), Button.left)
        amount = msg.get("amount", 1)
        try:
            amount = max(1, min(int(amount), 10))
        except (TypeError, ValueError):
            amount = 1

        async with _input_lock:
            if action == "click":
                _mouse.click(button)
                logging.info(f"MOUSE {button.name} click")
            elif action == "alt_click":
                try:
                    _keyboard.press(Key.alt_l)
                    _mouse.click(button)
                finally:
                    with contextlib.suppress(Exception):
                        _keyboard.release(Key.alt_l)
                logging.info(f"MOUSE alt+{button.name} click")
            elif action == "scroll_up":
                _mouse.scroll(0, amount)
                logging.info(f"MOUSE scroll up ({amount})")
            elif action == "scroll_down":
                _mouse.scroll(0, -amount)
                logging.info(f"MOUSE scroll down ({amount})")
            else:
                return False, f"unknown mouse action {action!r}"
        return True, None

    if kind == "ping":
        return True, None

    return False, f"unknown message type {kind!r}"


# ── WebSocket handler ─────────────────────────────────────────────────────────
class _RateLimiter:
    """Token bucket — caps how fast one connection can inject input."""

    def __init__(self, per_second: int):
        self.rate = per_second
        self.tokens = float(per_second)
        self.updated = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.rate, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


# Insertion-ordered, so the oldest connection can be evicted when at capacity.
_connections: dict = {}


async def _authenticate(ws, token: str) -> bool:
    """Verify the pairing token, from the request or a first auth message."""
    supplied = _token_from_request(ws)

    if supplied is None:
        # Fall back to an explicit handshake message, with a short deadline so
        # an idle unauthenticated socket can't sit on a connection slot.
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            if msg.get("type") != "auth":
                raise ValueError("expected auth message")
            supplied = str(msg.get("token", ""))
        except (asyncio.TimeoutError, json.JSONDecodeError, ValueError,
                websockets.exceptions.ConnectionClosed):
            supplied = ""

    if hmac.compare_digest(supplied or "", token):
        with contextlib.suppress(Exception):
            await ws.send(json.dumps({"type": "auth", "ok": True}))
        return True

    logging.warning(f"REJECTED unauthenticated connection from {ws.remote_address}")
    with contextlib.suppress(Exception):
        await ws.send(json.dumps({"type": "auth", "ok": False, "error": "bad token"}))
        await ws.close(code=4401, reason="unauthorized")
    return False


def make_handler(token: str | None):
    async def handle(ws: websockets.ServerConnection) -> None:
        # At capacity, evict the oldest connection rather than refusing the new
        # one. A phone that reconnects after a network blip would otherwise be
        # locked out by its own not-yet-reaped stale sockets.
        while len(_connections) >= MAX_CONNECTIONS:
            oldest = next(iter(_connections))
            _connections.pop(oldest, None)
            logging.warning(f"At capacity — dropping oldest connection {oldest.remote_address}")
            with contextlib.suppress(Exception):
                await oldest.close(code=1001, reason="replaced by newer connection")

        if token is not None and not await _authenticate(ws, token):
            return

        _connections[ws] = True
        limiter = _RateLimiter(MAX_MESSAGES_PER_SECOND)
        # remote_address becomes None once the socket closes — capture it now.
        peer = ws.remote_address
        logging.info(f"App connected  {peer}")
        pending: set[asyncio.Task] = set()

        try:
            async for raw in ws:
                if len(raw) > MAX_MESSAGE_BYTES:
                    logging.warning(f"Oversized message ({len(raw)} bytes) ignored")
                    continue
                if not limiter.allow():
                    logging.warning("Rate limit exceeded — message dropped")
                    continue

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logging.warning(f"Bad JSON: {raw!r}")
                    continue
                if not isinstance(msg, dict):
                    logging.warning(f"Expected a JSON object, got {type(msg).__name__}")
                    continue

                # Run the command as a task so a multi-second hold doesn't
                # stall reads; _input_lock keeps the injections ordered.
                task = asyncio.create_task(_run_command(ws, msg))
                pending.add(task)
                task.add_done_callback(pending.discard)

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            _connections.pop(ws, None)
            for task in list(pending):
                task.cancel()
            if pending:
                # Let the cancelled holds run their release handlers.
                await asyncio.gather(*pending, return_exceptions=True)
            logging.info(f"App disconnected  {peer}")

    return handle


async def _run_command(ws, msg: dict) -> None:
    """Execute one message and acknowledge it, never killing the connection."""
    req_id = msg.get("id")
    try:
        ok, error = await _dispatch(msg)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        ok, error = False, str(e)

    if not ok and error:
        logging.warning(error)

    # Only acknowledge messages that carry an "id". Clients predating the ack
    # protocol never send one and never expect an inbound frame, so this keeps
    # the wire byte-identical for them while newer clients opt in per message.
    if req_id is None:
        return
    reply = {"type": "ack", "ok": ok, "id": req_id}
    if error:
        reply["error"] = error
    with contextlib.suppress(Exception):
        await ws.send(json.dumps(reply))


# ── Entry point ───────────────────────────────────────────────────────────────
async def main(port: int = PORT) -> None:
    ips = _local_ips()
    ssl_ctx, cert_path = _ensure_ssl_context(ips)
    token = _ensure_token()

    # Bind before printing anything, so a failure to claim the port surfaces
    # as an error instead of appearing after a "ready" banner.
    server = await websockets.serve(
        make_handler(token), "0.0.0.0", port,
        ssl=ssl_ctx, max_size=MAX_MESSAGE_BYTES,
        ping_interval=PING_INTERVAL, ping_timeout=PING_TIMEOUT,
    )

    print()
    print("=" * 60)
    print("  StarCompanion Desktop Bridge  (WSS/TLS)")
    if len(ips) == 1:
        print(f"  Enter this IP in the app:  {ips[0]}")
    else:
        print("  Enter one of these IPs in the app (try the first):")
        for ip in ips:
            print(f"      {ip}")
    print(f"  Port: {port}")
    print(f"  Pairing token: {token}")
    print(f"  Cert SHA-256: {_cert_fingerprint(cert_path)}")
    print("=" * 60)
    print()

    uri = _pairing_uri(ips, port, token, _cert_fingerprint_b64(cert_path))
    if _print_qr(uri):
        print()
        print("  Scan this in the StarCompanion app to pair.")
    else:
        # No QR (piped output, or a console without ANSI support) — the URI
        # still pairs if the app can take it by paste or deep link.
        print(f"  Pairing link: {uri}")
    print()
    logging.info("Waiting for app to connect... (Star Citizen must be the active window)")

    async with server:
        await asyncio.Future()


def _parse_options() -> int:
    """Returns the port to listen on.

    The pairing token is mandatory. The old opt-outs (--insecure-no-auth, the
    STARCOMPANION_INSECURE_NO_AUTH env var, and the INSECURE_NO_AUTH sentinel
    file) are still recognised so an existing setup gets a clear message
    instead of silently starting with auth it no longer has.
    """
    parser = argparse.ArgumentParser(description="StarCompanion Desktop Bridge")
    parser.add_argument(
        "--insecure-no-auth", "--no-auth", action="store_true",
        dest="insecure_no_auth",
        help=argparse.SUPPRESS,  # retired — always errors out
    )
    parser.add_argument(
        "--port", type=int, default=PORT,
        help=f"port to listen on (default {PORT})",
    )
    # Ignore unrecognised args rather than exiting — a stray argument from a
    # shortcut or file association shouldn't stop the bridge from starting.
    args, unknown = parser.parse_known_args()
    if unknown:
        print(f"[warn] ignoring unrecognised argument(s): {' '.join(unknown)}")

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

    return args.port


def _refuse_no_auth(detail: str) -> None:
    print(f"\nERROR: {detail}")
    print("The pairing token is always required. Scan the QR in the startup")
    print("banner to pair without typing it.")
    # The pause is for a double-clicked window; suppress EOFError so a piped
    # or headless run exits cleanly instead of dumping a traceback.
    with contextlib.suppress(EOFError):
        input("\nPress Enter to close...")
    sys.exit(1)


if __name__ == "__main__":
    _port = _parse_options()
    try:
        asyncio.run(main(port=_port))
    except KeyboardInterrupt:
        print("\nBridge stopped.")
    except OSError as e:
        # WSAEADDRINUSE surfaces as winerror or as errno depending on how
        # asyncio re-raises it; EADDRINUSE is 48 on macOS, 98 on Linux.
        if getattr(e, "winerror", None) == 10048 or e.errno in (10048, 48, 98):
            print(f"\nERROR: port {_port} is already in use — is the bridge already running?")
        else:
            print(f"\nERROR: {e}")
        input("\nPress Enter to close...")
    except Exception as e:
        print(f"\nERROR: {e}")
        input("\nPress Enter to close...")
