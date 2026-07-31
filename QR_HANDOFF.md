# Handoff: QR pairing in the StarCompanion app

The bridge now prints a pairing QR on startup. This document is everything the
app side needs to consume it. It is self-contained — you do not need to read the
bridge source — but `HANDOFF.md` §3a carries the same payload table if you are
already working in that document, and §5 covers the connection sequence that
follows a successful scan.

Nothing here changes the wire protocol. Scanning replaces *typing four values
into a settings screen*; what happens after the app has an IP, port, token, and
fingerprint is unchanged.

---

## 1. Why this exists

Today the user reads a 24-character token off a monitor and types it into a
phone, then guesses which of several IP addresses is reachable. Both steps fail
often enough to be the top pairing complaint. The QR removes both.

It also carries the certificate fingerprint, which the user has no practical way
to transcribe by hand. That makes pinning realistic for the first time — the QR
is a security improvement, not only a convenience one.

---

## 2. The payload

A single URI, encoded in the QR:

```
starcompanion://pair?v=2&h=192.168.1.42&h=10.0.0.4&p=8765&t=<token>&fp=<fingerprint>
```

| Param | Repeats? | Meaning |
|---|---|---|
| `v` | no | Payload version, currently `2`. |
| `h` | **yes** | Candidate host — one per LAN IPv4 the bridge detected. |
| `p` | no | Port. |
| `t` | no | Pairing token. Always present. |
| `fp` | no | Certificate SHA-256, base64url, no padding (43 chars). |

Typical length is ~130 characters, which produces a version 6 symbol (45×45
modules) at error-correction level L.

### Handling each field

**`v` — reject what you don't know.** If `v` is missing or is a value this build
doesn't handle, show "update StarCompanion to pair with this bridge" and stop.
Do not attempt a best-effort parse of an unknown version; the point of the field
is that future payloads can add or change parameters safely.

**`h` — repeats, and order is a hint.** The bridge emits every address it found
because it cannot tell which one the phone can reach. A PC with a VPN, Hyper-V,
WSL, or Docker adapter routinely reports three or four, and the reachable one is
frequently not the first.

Race them: attempt all candidates concurrently, or sequentially with a short
(~2s) timeout each, and keep the first that completes a TLS handshake *and*
authenticates. Persist the winner, try it first on later launches, and fall back
to the full list when it fails — the user's DHCP lease will eventually move.

Do not present the user a list to choose from. Guessing which IP is reachable is
the exact problem the QR exists to eliminate.

**`t` — always present.** The pairing token is mandatory on the bridge side and
cannot be turned off, so a payload without `t` did not come from a current
bridge. Treat it as malformed and refuse it rather than falling back to an
unauthenticated connection.

**`fp` — base64url, not the hex in the banner.** The startup banner prints the
colon-separated hex form for humans; the QR carries the same 32 bytes as
unpadded base64url to keep the symbol small. Decode to raw bytes before
comparing — do not string-compare against a hex fingerprint.

---

## 3. Parsing

Use a query parser that **preserves duplicate keys** (`URLComponents` /
`queryItems` on iOS, `Uri.getQueryParameters(name)` on Android). 

A "last value wins" parse — a plain dictionary, `Uri.getQueryParameter()`
singular, or most naive hand-rolled splits — silently keeps only the final `h`
and discards the rest. The result is not a visible error: the app ends up
pointed at whichever adapter the bridge happened to list last, which on a
developer machine is usually a VPN or Hyper-V address that the phone cannot
reach. It looks like "the bridge is offline." Test with a multi-IP payload.

Percent-decode values after splitting. The token is URL-safe base64 and the
fingerprint is base64url, so neither *normally* contains characters needing
escapes, but the bridge percent-encodes on the way out and you should decode on
the way in rather than relying on that.

---

## 4. Scanning

- **Camera permission**: request it at the moment the user taps "Scan", with a
  purpose string that says it's for pairing. Handle denial by falling back to
  manual entry rather than dead-ending.
- **The QR is on a monitor**, not paper. Glare, low brightness, and aggressive
  display scaling all degrade it. Autofocus and a reasonable minimum resolution
  matter more than they would for a printed code.
- **Validate before applying**: parse fully, check `v`, and confirm at least one
  `h` and a `p` are present before overwriting saved connection settings. A
  partial write leaves the app in a worse state than before the scan.
- **Ignore non-matching QRs** quietly. Users will point the camera at arbitrary
  codes; only act on the `starcompanion://pair` scheme and path.

---

## 5. Keep the manual paths

The QR is the primary path, not the only one. Two fallbacks are cheap and worth
having:

1. **Pasted link.** When the console cannot render a QR — output redirected to a
   file, or a terminal without ANSI support — the bridge prints the identical
   `starcompanion://pair?…` URI as text. Accepting a pasted link (and, if you
   want, registering the scheme as a deep link) reuses the parser you already
   wrote. On a phone this also covers the user who takes a screenshot rather
   than scanning.
2. **Typed fields.** IP, port, and token entry must stay. A headless bridge over
   SSH, a phone with a broken camera, or a user reading values off a support
   call all need it.

---

## 6. After a successful scan

Persist the token alongside the chosen host — it is stable across bridge
restarts, so the app should never prompt again unless authentication fails.

Persist the fingerprint if you implement pinning, and treat a **mismatch as
"re-pair required," not a permanent failure**. The bridge reissues its
certificate automatically whenever the PC's LAN IP changes, so a pin can go
stale through nothing more than a new DHCP lease. Prompt the user to re-scan.

See `HANDOFF.md` §5 for the connection sequence and §4 for the TLS details.

---

## 7. Test checklist

- [ ] Multi-IP payload: all `h` values parsed, unreachable ones skipped, reachable one connects.
- [ ] Reachable address listed **last** — catches a last-value-wins parse.
- [ ] Payload with no `t`: refused as malformed, no unauthenticated fallback.
- [ ] Payload with an unrecognized `v`: refused with an update prompt.
- [ ] Fingerprint pinning: matching cert connects; changed cert prompts re-pair.
- [ ] Saved host stops working: app falls back to the other candidates.
- [ ] Camera permission denied: manual entry still reachable.
- [ ] Pasted `starcompanion://` link produces the same result as a scan.
