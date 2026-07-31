# Handoff: updating the StarCompanion app for Bridge v2

Context for whoever picks up the iOS/Android client work. The desktop bridge
(`bridge_server.py`) was reworked for security and reliability. **The current app
will no longer connect** — it gets closed with code `4401` immediately after the
TLS handshake, because it doesn't send a pairing token.

This doc is the client-side spec. Everything here is verified against
`bridge_server.py` in this repo; file/line pointers are given so you can check
the source rather than trust the prose.

---

## 1. What broke and why

The old bridge accepted any client that completed a TLS handshake on
`0.0.0.0:8765`. Since the cert is self-signed, the app can't have been
validating it, which means anything on the LAN — a guest phone, a compromised
IoT device, a web page on the same machine — could connect and inject arbitrary
keystrokes into whatever window had focus. `WIN` is a supported modifier, so
`WIN+R` was one WebSocket frame away from arbitrary code execution.

The fix is a shared pairing token. That's the breaking change. Everything else
listed below is additive and backward-compatible at the wire level.

---

## 2. Minimum viable change

If you want the smallest possible diff to get the app working again:

1. Add a **Pairing token** text field next to the IP address field in connection
   settings.
2. Append `?token=<urlencoded token>` to the WebSocket URL.
3. Handle the server's first frame, `{"type":"auth","ok":true}`, before
   considering the connection ready.

That's it. The app can keep ignoring `ack` frames if you're in a hurry — they're
purely additive. Sections 5–8 describe the improvements worth doing properly.

---

## 3. What the user sees on the desktop

The bridge prints this banner on startup:

```
============================================================
  StarCompanion Desktop Bridge  (WSS/TLS)
  Enter this IP in the app:  192.168.1.42
  Port: 8765
  Pairing token: EXAMPLE-TOKEN-not-a-real-one
  Cert SHA-256: AA:BB:CC:...:EE:FF
============================================================
```

Three things the app may need to collect:

| Value | Required? | Notes |
|---|---|---|
| IP address | yes | Banner may list **several** IPs if the PC has a VPN, Hyper-V, WSL, or Docker adapter. Tell the user to try the first. |
| Pairing token | yes | 24-char URL-safe string, stable across restarts. Persisted in `bridge_token.txt` next to the exe. |
| Cert SHA-256 | optional | Only if you implement pinning (§4). |

The token is stable, so the app should **persist it** alongside the saved IP and
never prompt again unless auth fails.

The banner is followed by a **pairing QR** (§3a) that carries all three values,
so hand-entry should be the fallback path, not the primary one.

---

## 3a. Pairing QR

The bridge always prints a QR below the banner (`_print_qr`, `bridge_server.py`).
Scanning it should populate every connection field in one step. **`QR_HANDOFF.md`
covers the app-side work in full** — parsing, scanning, fallbacks, and a test
checklist; the summary below is enough if you only need the payload shape.

Encoded payload:

```
starcompanion://pair?v=2&h=192.168.1.42&h=10.0.0.4&p=8765&t=<token>&fp=<fingerprint>
```

| Param | Repeats? | Meaning |
|---|---|---|
| `v` | no | Payload version, currently `2`. **Reject payloads with an unknown `v`** and tell the user to update the app. |
| `h` | **yes** | Candidate host. One per detected LAN IPv4 — see below. |
| `p` | no | Port. Don't assume 8765; the bridge takes `--port`. |
| `t` | no | Pairing token. Always present — the bridge cannot run without one. Treat a payload with no `t` as malformed. |
| `fp` | no | Cert SHA-256, **base64url without padding** (43 chars) — not the colon-hex form shown in the banner. Decode to 32 raw bytes to compare against the presented cert. |

**`h` repeats and the order is a hint, not an answer.** The bridge cannot tell
which of its addresses the phone can actually reach, so it emits all of them.
Race them — attempt each concurrently (or in listed order with a short timeout)
and keep the first that completes a TLS handshake and auth. Persist the winner
and try it first next time, falling back to the full list on failure. Do not
show the user a picker; that's the problem the QR exists to remove.

Parse with a standard query parser that preserves duplicate keys. Note that a
naive "last value wins" parse silently discards every host but the last one,
which usually leaves the app pointed at a VPN or Hyper-V address that can't be
reached from the phone.

**Also keep manual entry.** A phone camera can't always read a monitor (glare,
scaling, a bridge running headless over SSH). When the console can't render —
output is piped, or the terminal lacks ANSI support — the bridge prints the same
URI as text instead, so accepting a pasted `starcompanion://` link covers that
case cheaply.

---

## 4. TLS

Unchanged in spirit, but the cert is now better-formed:

- Self-signed, RSA-2048, 10-year validity.
- SAN now includes `localhost`, `127.0.0.1`, **and every detected LAN IPv4**
  (`_generate_cert`, `bridge_server.py:244`). Previously it only had
  `DNSName: localhost`, so hostname verification could never succeed.
- The cert is **automatically reissued** if the PC's LAN IP changes
  (`_cert_covers` / `_ensure_ssl_context`, `bridge_server.py:286`).

**Implication for the app:** if you implement fingerprint pinning, the pin can
change out from under you when the user's DHCP lease or network changes. Handle a
pin mismatch as "re-pair required" (prompt the user to re-scan / re-enter),
**not** as a hard permanent failure. If you're not pinning, keep whatever
trust-all behavior you have today — but pinning plus the token is the
combination that actually makes this secure, so it's worth doing.

---

## 5. Connection sequence

### Option A — query string (recommended for mobile)

```
wss://<ip>:8765/?token=<urlencoded-token>
```

Works on every mobile WebSocket stack, including ones that can't set custom
headers. This is the path most clients should take.

### Option B — header

```
Authorization: Bearer <token>
```

Cleaner (keeps the secret out of the URL) if your WebSocket library supports
custom headers. Both are checked in `_token_from_request`
(`bridge_server.py:225`).

### Option C — handshake message

If neither is present, the server waits **up to 10 seconds** for a first frame:

```json
{"type": "auth", "token": "EXAMPLE-TOKEN-not-a-real-one"}
```

After 10s of silence it treats the connection as unauthenticated and closes it.

### Server response

On success, the server sends exactly one frame before anything else:

```json
{"type": "auth", "ok": true}
```

On failure:

```json
{"type": "auth", "ok": false, "error": "bad token"}
```

…immediately followed by a close with code **`4401`** and reason
`"unauthorized"`.

**Do not send commands before you've seen `{"type":"auth","ok":true}`.** With
options A and B, auth completes during the handshake and the frame arrives
essentially instantly, but gating on it keeps the client correct across all three
paths.

---

## 6. Message protocol

Every message the app sends now gets an acknowledgement. Attach an `id` (any
JSON value; an incrementing integer is fine) and the server echoes it back, so
you can correlate replies to requests.

### Keyboard

```json
{"type":"command","action":"landing_gear","key":"N","hold":null,"id":17}
```

| Field | Type | Notes |
|---|---|---|
| `action` | string | Free-form label. Logged on the desktop; not interpreted. |
| `key` | string | `MOD+KEY` syntax, see §7. Required, non-empty. |
| `hold` | number, optional | Seconds. **Clamped server-side to 10s max.** `<= 0` is treated as a plain press. Non-numeric returns an error ack. |
| `id` | any, optional | Echoed in the ack. |

### Mouse

```json
{"type":"mouse","action":"scroll_up","button":"left","amount":3,"id":18}
```

| Field | Values | Notes |
|---|---|---|
| `action` | `click`, `alt_click`, `scroll_up`, `scroll_down` | Required. |
| `button` | `left` (default), `right`, `middle` | New — the old bridge was left-only. |
| `amount` | 1–10, default 1 | Scroll only. Clamped. |

### Ping

```json
{"type":"ping","id":19}
```

Cheap round-trip; injects nothing. Useful for a connection-health indicator.
Note this is a *protocol-level* ping distinct from WebSocket control-frame pings,
which the `websockets` library handles on its own — you don't need this for
keepalive, only if you want an app-visible liveness check.

### Acknowledgement

```json
{"type":"ack","ok":false,"id":17,"error":"unknown key 'NOPE' for action 'landing_gear'"}
```

**Acks are opt-in: the server replies only to messages that carried an `id`.** A
client that sends no `id` receives no inbound frames whatsoever, which is what
keeps pre-v2 clients working. Send an `id` on every message. `error` is present
only when `ok` is `false`.

**This is the most valuable new capability for the app.** Previously an
unrecognised key was logged on the desktop and silently dropped — the user got no
feedback and no way to tell a bad keybind from a bad connection. Now the
Edit Keybinds screen can validate a binding by sending it and checking the ack.
Consider surfacing `ok:false` as an inline error on the offending keybind row.

---

## 7. Key syntax

Unchanged format, wider coverage. `MOD+KEY`, uppercase-insensitive.

**Modifiers:** `ALT` `LALT` `RALT` `CTRL` `LCTRL` `RCTRL` `SHIFT` `LSHIFT`
`RSHIFT` `WIN` `META`. (`RSHIFT` is new.)

**Named keys:** `F1`–`F12` `CAPS` `ESC` `TAB` `SPACE` `ENTER` `BACKSPACE`
`DELETE` `INSERT` `HOME` `END` `PAGEUP` `PAGEDOWN` `UP` `DOWN` `LEFT` `RIGHT`
`NUM_LOCK` `SCROLL_LOCK` `PAUSE` `PRINT_SCREEN`.

**Single characters:** letters, digits, and punctuation.

> ⚠️ **Punctuation bindings were broken before and now work.** The old bridge used
> `ord(char)` as the Windows virtual-key code, which is only correct for A–Z and
> 0–9. `\` sent VK `92` instead of `0xDC`, so it pressed the wrong key entirely.
> The bridge now resolves punctuation via `VkKeyScanW` against the active
> keyboard layout (`_vk_for_char`, `bridge_server.py:319`).
>
> **If any user-saved keybinds contain punctuation, they may have been "corrected"
> by users to compensate for the old bug.** Worth a release note, and worth
> checking whether your default keybind set ships any punctuation bindings — Star
> Citizen's defaults use `[`, `]`, and `\` in a few places.

Edge case: a literal `+` as the key works — `CTRL++` parses as CTRL plus the `+`
key.

---

## 8. Limits and error handling

| Limit | Value | Behavior on breach |
|---|---|---|
| Concurrent connections | 4 | The **oldest** connection is closed with `1001` to make room; the new one is accepted |
| Messages per second, per connection | 100 (token bucket) | Message dropped (logged desktop-side), connection stays open |
| Message size | 4096 bytes | Dropped; oversized frames also rejected by the WS layer |
| Hold duration | 10s | Clamped, not rejected |

Close codes the app should distinguish:

- **`4401`** — bad or missing token. Prompt for re-pairing. Do **not** auto-retry
  in a loop; you'll just spam rejections.
- **`1001`** — this connection was evicted because a newer one arrived (usually
  the same phone reconnecting). Don't surface this as an error to the user; if
  it's genuinely a stale socket you're replacing, just let it go.
- Anything else — normal reconnect logic.

### Keepalive and idle behavior

The bridge pings every 20s and **never closes a connection for a missed pong**
(`PING_INTERVAL` / `PING_TIMEOUT`, `bridge_server.py:84`). An idle or backgrounded
app will not be disconnected by the server. Verified: a client that ignored seven
consecutive pings over 150s stayed connected.

The app still needs its own reconnect logic, because the server can't prevent
drops it doesn't cause — mobile OS socket teardown on backgrounding, WiFi/cellular
handoff, or a router NAT timeout. Recommended client behavior:

- Reconnect automatically with backoff on any unexpected close.
- Don't treat a close as a pairing failure unless the code is `4401`.
- Optionally send `{"type":"ping","id":N}` when the app returns to the foreground
  to confirm the socket is genuinely alive before the user taps a control — a
  socket can look open locally while being long dead.

Robustness notes worth knowing: malformed JSON, unknown message types, bad `hold`
values, and non-object payloads no longer kill the connection — the server logs
them, returns an error ack where applicable, and keeps going. Commands also run
as independent tasks now, so a 10-second hold no longer blocks subsequent
commands from being read (they still execute in order behind a lock).

---

## 9. Migration strategy

**The token is mandatory — there is no way to run the bridge without it.** The
old opt-outs (`--insecure-no-auth`, `STARCOMPANION_INSECURE_NO_AUTH=1`, and the
`INSECURE_NO_AUTH` sentinel file) now refuse to start rather than launching an
unauthenticated bridge, so "ship the bridge first and tell users to disable auth"
is no longer an option. Note that acks are opt-in (§6), so a pre-v2 client that
sends no `id` is otherwise unaffected.

That leaves one path: **ship both together.** The app can be made to work with
either version — if the connection closes with `4401`, show the pairing prompt;
if it connects and works without a token, the user is on an old bridge, and a
soft "your desktop bridge is out of date" nudge is a nice touch.

Version negotiation isn't implemented (there's no version field in the protocol),
so behavior-sniffing via the `4401` close code is the available mechanism. If
you'd rather have an explicit version handshake, that's a small desktop-side
addition — say the word.

---

## 10. Test checklist

- [ ] Fresh pair: enter IP + token → connects, receives `{"type":"auth","ok":true}`
- [ ] Wrong token → close `4401`, app prompts for re-pairing, does not retry-loop
- [ ] Token persisted across app restart; no re-prompt
- [ ] Command ack correlation: send 3 commands with distinct `id`s, all 3 acks matched
- [ ] Unknown key → `ok:false` ack surfaces as an inline keybind error
- [ ] Hold command: verify the key releases in-game (send `hold: 3`)
- [ ] Kill the app mid-hold → key does **not** stay stuck down in-game
- [ ] Punctuation keybind (`\`, `[`, `-`) actually triggers the right in-game action
- [ ] Desktop IP changes (toggle VPN) → cert reissues, app re-pairs cleanly
- [ ] Old (pre-token) bridge → app still connects, shows the out-of-date nudge (§9)

---

## Source map

| Concern | Location |
|---|---|
| Token generation / lookup | `bridge_server.py:212` `_ensure_token`, `:225` `_token_from_request` |
| Auth handshake | `bridge_server.py:495` `_authenticate` |
| Cert generation, SAN, reissue | `bridge_server.py:244` `_generate_cert`, `:286` `_cert_covers`, `:305` `_ensure_ssl_context` |
| Key string → VK resolution | `bridge_server.py:319` `_vk_for_char`, `:341` `_resolve` |
| Message dispatch, ack shape | `bridge_server.py:415` `_dispatch`, `:579` `_run_command` |
| Limits | `bridge_server.py:72`–`77` (constants) |
