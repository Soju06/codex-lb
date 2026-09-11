# Change: scope-outbound-thread-identity-per-account

## Why

One logical downstream thread can be served by several pooled ChatGPT accounts.
The identifiers that name that thread travel upstream unchanged: the outbound
`prompt_cache_key` is either the client's value or the one
`_derive_prompt_cache_key` computed, and the session/conversation headers
(`session_id`, `session-id`, `x-codex-session-id`, `x-codex-conversation-id`,
`thread-id`, plus the opencode set `x-parent-session-id`, `x-opencode-session`,
`x-session-id`, `x-session-affinity`) are not in `IGNORE_INBOUND_HEADERS` and
are forwarded verbatim by every header builder. So account A and account B
present the *same* identifier for the same thread to an upstream that scopes
those resources per account.

That is the same defect `apply_codex_installation_metadata` already fixes for
the Codex installation id under "Codex installation metadata must be
account-owned". Rewriting identifier fields and headers is defensible: they
name account-scoped resources upstream. Rewriting *content* is not, and is
explicitly out of scope here.

This change does **not** claim to fix the unkeyed-traffic cache-hit defect
(#2347). Requests with no continuity key switch accounts far more often and
cache far less; that is a derivation problem in `_derive_prompt_cache_key` and
needs its own change. What this change does is make the identifier each account
presents correct, and give a stable per-account cache anchor once it is on.

## What Changes

- New pure module `app/core/clients/account_scoped_identity.py`: a frozen
  namespace UUID and a deterministic, clock-free, randomness-free mapping
  `scope_thread_value(value, installation_id)`. A UUID-shaped input maps to a
  UUID-shaped output (`uuid5` over length-framed material); anything else maps
  to a `cxlb-`-prefixed 128-bit digest. The salt is the per-seat
  `accounts.codex_installation_id`, never the nullable per-workspace
  `chatgpt_account_id` that every seat of a Team/Business org shares.
  The mapping is deliberately **not** idempotent, so it is applied exactly once
  per outbound request, at dispatch.
- The three outbound header builders (`_build_upstream_headers`,
  `_build_upstream_websocket_headers` in `app/core/clients/proxy.py`, and
  `_build_upstream_websocket_headers` in `app/core/clients/proxy_websocket.py`)
  take a `scoped_identity_installation_id` keyword and scope the session/thread
  header vocabulary. Scoping lives in the builders, not in
  `apply_codex_installation_headers`, because that post-filter runs twice on the
  HTTP and per-request-WebSocket paths and a twice-scoped value would differ
  from the once-scoped value the persistent bridge presents for the same thread.
- The outbound body `prompt_cache_key` (and the `client_metadata` mirrors of
  `x-codex-parent-thread-id` / `x-codex-window-id`, so the header and body
  surfaces agree) is scoped at four dispatch points: the
  `_stream_responses_with_session` chokepoint, which covers the HTTP transport,
  the per-request WebSocket transport and the websocket-rejection fallback; the
  HTTP session bridge send boundary; the direct-WebSocket send boundary; and the
  compact transport, which had no per-account stamping at all.
- Routing is untouched. `_resolve_prompt_cache_key` still writes the
  account-neutral derived key onto the request model *before* account selection,
  and `_sticky_key_for_responses_request` still reads it there. The scoped value
  is written only onto the outbound copy, after selection, so selection cannot
  shatter into per-account lanes.
- Continuity tokens are untouched. `x-codex-turn-state` and
  `previous_response_id` are upstream-issued opaque values that must round-trip
  verbatim; the scoping vocabulary excludes them by construction.
- Everything is gated on a new default-off T3 setting,
  `account_scoped_thread_identity_enabled`: nullable `dashboard_settings`
  column (alembic `20260911_000000_dashboard_account_scoped_thread_identity`),
  `SETTING_TIERS` entry, `DASHBOARD_SWITCH_SETTINGS` so the hot path sees the
  dashboard value without a database read, `GET`/`PUT /api/settings` with
  provenance and the tri-state update, a dashboard card, and
  `[settings_fields].max` 96 -> 97.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `upstream-proxy-routing`: ADDED requirement "Outbound thread identity must be
  account-scoped" — the per-account rewrite, its salt, its determinism and shape
  contract, the excluded continuity tokens, and its separation from selection.
- `responses-api-compat`: MODIFIED "Use prompt_cache_key as OpenAI cache
  affinity" and "Codex backend session_id preserves account affinity" — both
  currently mandate unchanged forwarding; they now mandate unchanged forwarding
  while the flag is off and an account-scoped value at dispatch while it is on,
  with the scoped value stable per (account, logical thread) and the routing
  input still account-neutral.
- `configuration-tiers`: unchanged. This change follows its existing procedure
  for adding a T3 field, including raising `[settings_fields].max` in the same
  diff.

## Impact

- Schema: one nullable boolean column on `dashboard_settings` (SQLite and
  PostgreSQL, guarded add/drop).
- API: additive `accountScopedThreadIdentityEnabled` plus
  `provenance.account_scoped_thread_identity_enabled` on `GET`/`PUT
  /api/settings`.
- Operators: no action. The flag defaults off and the off path is a
  byte-for-byte no-op on the outbound request, asserted by test. Enabling it
  costs one cache miss per live thread as each thread re-anchors; disabling it
  restores the previous identifiers exactly.
- Deliberately out of scope: injecting any per-account token into prompt
  *content* (no correctness or caching justification, pollutes authored text,
  can leak into model output), and `/transcribe`, `/files` and the generic
  Codex control egress, which forward `x-codex-*` headers wholesale and receive
  no per-account post-processing today. The invariant this change establishes
  holds on the Responses surfaces it names, not globally.
