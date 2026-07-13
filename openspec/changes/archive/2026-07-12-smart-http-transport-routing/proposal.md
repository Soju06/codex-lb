# Smart HTTP→upstream transport routing

## Why

Today every downstream HTTP/SSE request that resolves to an upstream
`websocket` transport is **unconditionally pinned to upstream HTTP**
(`service.py`, `_stream_with_retry`):

```python
if request_transport == _REQUEST_TRANSPORT_HTTP and upstream_stream_transport == "websocket":
    # HTTP/SSE clients can retry a half-rendered turn after an upstream
    # websocket close, making the same visible message restart.
    upstream_stream_transport = "http"
```

That pin (#698) exists to avoid the "half-rendered turn restart" hazard:
an HTTP/SSE client retrying after an upstream WS close could re-render a
partially emitted turn. But it throws away the WebSocket path's main
operational benefit — **upstream `server_is_overloaded` rejections are
absorbable on the WS path**, and our overload-absorption retry ladder
lives there.

Production evidence (request_logs, 1790 `server_is_overloaded` samples):

- **1789 / 1790 (99.9%) of overload rejections happen before the first
  token is emitted** (`output_tokens == 0`, no `response.*` item event).
- Only **1 / 1790 (0.06%)** rejected after partial output.

So the hazard the pin protects against ("half-rendered turn restart") is
statistically negligible in practice, while the pin's cost — denying
**all** downstream-HTTP traffic the WS overload-absorption ladder — is
paid on every overloaded turn.

At the same time, a blanket "send all HTTP over WS" is wrong too. The WS
upstream path pays a per-request handshake **plus** an admission-control
slot (`response_create_gate`), and prewarm only amortizes that across a
**multi-turn** session (prewarm fires only when `previous_response_id is
None`). A genuinely **single-shot** caller (e.g. a one-off
`soju-graphiti` key with no session continuity) would eat the full WS
setup overhead on every call with nothing to amortize it against.

The right rule is **transport selection by request shape**: send
session-continuation / cache-affine ("sticky") requests over WebSocket so
they get prewarm amortization and overload absorption; send genuinely
single-shot requests over HTTP where the lighter POST-and-reuse path
wins.

## What changes

Replace the unconditional downstream-HTTP→upstream-HTTP pin with a
**configurable transport routing policy** that decides per request:

1. **New global setting** `http_downstream_transport_policy` with four
   values:
   - `smart` (**new default**) — route by sticky-session signal (policy
     **B** below).
   - `always_http` — preserve today's behavior (pin every downstream-HTTP
     request to upstream HTTP).
   - `always_websocket` — never downgrade; downstream HTTP may use
     upstream WS whenever the base transport resolves to WS.
   - `pinned` — explicit alias of `always_http` for operators who want to
     name the legacy #698 behavior.

2. **Policy B — sticky-session signal.** Under `smart`, a downstream-HTTP
   request whose base transport resolves to `websocket` keeps upstream
   WebSocket **iff** any sticky-continuation signal is present:
   - `payload.previous_response_id` is set, **OR**
   - a `prompt_cache_key` is present on the request model
     (`_prompt_cache_key_from_request_model`), **OR**
   - a Codex session header is present
     (`_sticky_key_from_session_header`), **OR**
   - an `x-codex-turn-state` continuity header is present
     (`_sticky_key_from_turn_state_header`).

   Otherwise (no sticky signal = single-shot) it falls back to upstream
   HTTP, exactly as today.

3. **Per-API-key override.** A new optional `transport_policy_override`
   on the API key record. When set it wins over the global policy **for
   requests authenticated by that key**; when null the key follows the
   global default. Values: `smart | always_http | always_websocket`
   (no separate `pinned` alias at key scope — `always_http` covers it).

The existing precedence rails are preserved and sit **above** this
policy:

- Explicit `upstream_stream_transport = "websocket" | "http"` config
  override still wins outright.
- Oversized-payload → HTTP and image / image-generation → HTTP bypasses
  still force HTTP regardless of policy.
- Native Codex WebSocket clients (`request_transport == websocket`) are
  unaffected — they keep their dedicated WS path; this change only
  governs the **downstream-HTTP** branch.

No behavior change for native WS clients, oversized payloads, or image
requests. The only behavioral delta is: under the new `smart` default,
sticky downstream-HTTP turns now ride upstream WebSocket (gaining the
overload-absorption ladder) instead of being pinned to HTTP.

## Impact

- **Affected specs:** `responses-api-compat` (transport resolution
  requirements), `api-keys` (new per-key override field + schema).
- **Affected code:**
  - `app/modules/proxy/service.py` — replace the unconditional pin in
    `_stream_with_retry` with a policy-aware decision; thread the
    per-key override and sticky-signal inputs.
  - `app/core/config` (Settings) — add `http_downstream_transport_policy`.
  - API key model + schema + migration — add
    `transport_policy_override` (nullable).
  - Dashboard settings + API-key edit surfaces — expose the global
    policy dropdown and the per-key override (null = "follow global").
- **Migration:** additive nullable column on the API key table; existing
  rows default to null (= follow global). Default global policy ships as
  `smart`, which **changes runtime behavior** for sticky downstream-HTTP
  turns — call this out in release notes.
- **Risk:** the half-rendered-turn-restart hazard the #698 pin guarded
  is re-opened only for sticky turns on `smart`. Mitigated by the 99.9%
  "overload-before-first-token" evidence and by leaving `always_http`
  one setting away for any operator who wants the legacy guarantee back.
