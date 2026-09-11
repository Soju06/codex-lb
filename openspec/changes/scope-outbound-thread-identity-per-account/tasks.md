# Tasks

## 1. Scoping primitive

- [x] 1.1 `app/core/clients/account_scoped_identity.py`: frozen namespace UUID,
      `scope_thread_value`, `scope_prompt_cache_key`, `scope_session_headers`,
      `scope_payload_thread_identity`; the scoped header/body vocabulary; the
      never-scoped set (`x-codex-turn-state`, `previous_response_id`).
- [x] 1.2 No randomness, no clock, no process state; UUID-shaped in means
      UUID-shaped out; length-framed material so two accounts cannot collide.

## 2. Feature flag (T3)

- [x] 2.1 `account_scoped_thread_identity_enabled: bool = False` in `Settings`;
      `SETTING_TIERS` entry `"T3"`; `[settings_fields].max` 96 -> 97 with the
      why-not-a-default justification.
- [x] 2.2 Nullable `dashboard_settings` boolean column; alembic
      `20260911_000000_dashboard_account_scoped_thread_identity`
      (`down_revision = 20260910_020000_add_dashboard_role_mappings`).
- [x] 2.3 Settings schemas / repository / service / api: effective value,
      provenance, tri-state update, audit of an ownership-only change.
- [x] 2.4 `DASHBOARD_SWITCH_SETTINGS` so `with_dashboard_overrides` applies the
      dashboard value on the dispatch path with no database read.
- [x] 2.5 Dashboard card plus `en` / `ko` / `zh-CN` strings; regenerate
      `docs/reference/settings.md`.

## 3. Dispatch wiring

- [x] 3.1 Headers: the three builders take `scoped_identity_installation_id`;
      threaded from the HTTP, per-request-WebSocket, websocket-rejection
      fallback, compact and persistent-WebSocket handshake call sites.
      `apply_codex_installation_headers` is left alone (it runs twice).
- [x] 3.2 Body path A: `_stream_responses_with_session`, right after
      `apply_codex_installation_metadata`, before `http_payload_dict` and
      `websocket_payload_dict` are derived.
- [x] 3.3 Body path B: the HTTP bridge send boundary
      (`_send_http_bridge_request_text_with_archive_id`), send-only, never
      written back to `request_state.request_text`, so the durable operation
      fingerprint stays account-neutral.
- [x] 3.4 Body path C: the single direct-WebSocket upstream send, after the
      dispatch-owner binding, with the response.create size limit re-checked.
- [x] 3.5 Body path D: `compact_responses` / `_CompactCommandTransport` gain a
      `codex_installation_id` keyword, passed through the existing optional
      kwargs dict.

## 4. Invariants

- [x] 4.1 Selection stays account-independent: nothing writes the scoped value
      onto the request model, so `_sticky_key_for_responses_request` returns the
      identical key with the flag on and off.
- [x] 4.2 `x-codex-turn-state` and `previous_response_id` are never rewritten.

## 5. Verification

- [x] 5.1 Unit: determinism, per-account divergence, UUID-shape preservation,
      non-UUID prefix form, empty/blank passthrough, no-salt passthrough.
- [x] 5.2 Unit: all three header builders scope the vocabulary, leave turn state
      untouched, and are byte-for-byte identical to today with the flag off.
- [x] 5.3 Unit: body chokepoint scopes the outbound copy only; sticky-key
      invariance; bridge durable fingerprint unchanged.
- [x] 5.4 Settings: resolver states, API round trip, migration upgrade/downgrade.
- [x] 5.5 `ruff check` / `ruff format`, `make lint` ratchets,
      `openspec validate scope-outbound-thread-identity-per-account --strict`.
