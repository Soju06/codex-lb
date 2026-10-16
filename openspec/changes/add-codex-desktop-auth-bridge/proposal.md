# Add an opt-in Codex Desktop authentication bridge

## Why

Codex Desktop signed in with a ChatGPT login sends its own ChatGPT access token
to the configured OpenAI base URL. Pointing that base URL at codex-lb therefore
presents a ChatGPT token where codex-lb expects an LB API key, so a Desktop that
wants ChatGPT-login client behavior cannot authenticate to an API-key-protected
codex-lb without manual header surgery. Public issue #2262 asks for a supported
way to do this. This change is an independent implementation related to that
issue; it does not claim to resolve every Desktop surface it mentions.

## What Changes

- Add a standalone, opt-in `codex-lb desktop-bridge` loopback relay
  (`app/desktop_bridge/`). It listens only on `127.0.0.1` and forwards only the
  `/backend-api/codex` namespace to codex-lb on `127.0.0.1:<lb_port>`.
- The bridge admits a request only when its single bearer token equals the
  `tokens.access_token` in the local Codex `auth.json`, then replaces every
  inbound credential, cookie, and forwarding header with
  `Authorization: Bearer <LB token>` read from a private token file.
- HTTP bodies and WebSocket messages are relayed opaquely and bounded: no
  decompression, cookies, environment proxies, automatic client headers, or
  redirects. Upstream 3xx and 401 become a generic 502.
- Bound process shutdown while HTTP or WebSocket relays are active: cancel and
  await owned relay work, close both connections, and release request slots.
- No `CODEX_LB_*` setting, environment knob, database, dashboard, or main-server
  change. Codex Desktop keeps the built-in OpenAI provider via
  `openai_base_url`; no client feature is disabled.

## Impact

- New capability: `desktop-auth-bridge`.
- New code: `app/desktop_bridge/` (credentials, request policy, relay). CLI
  wiring and user docs land in the same change under `app/cli.py` and
  `docs/desktop-bridge.md`.
- Existing proxy routing, account selection, and API-key semantics are
  unchanged; the bridge is an ordinary API-key client of codex-lb.
- Simplicity: off by default (P1); no new settings (P2); docs go to `docs/` and
  OpenSpec, not the README (P4); nothing dashboard-visible (P5).
- Scope limits: native OAuth file storage (`auth.json`) only. Agent-identity,
  keyring-only, API-key-mode, and externally managed token stores fail closed.
  Local acceptance covers model discovery, a normal turn, and a prediction-mode
  fork/turn. A separate manual macOS test with Desktop `26.1007.30912`, runtime
  `0.162.0-alpha.17.2`, and stable CodexLB `1.24.0` confirmed that a Composer
  suggestion appeared automatically in the same thread after about 10 seconds
  and Tab accepted it. This is one tested setup and a user-observed timing, not
  a latency guarantee. Realtime, mobile, Windows, and remote-host behavior
  remain unverified.
