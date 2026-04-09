## Overview

HTTP bridge continuity is no longer modeled as "live owner pod memory only".
This change introduces a durable continuity journal in Postgres that tracks:

- canonical bridge session keys
- alias mappings for `x-codex-turn-state`, `x-codex-session-id`, and `previous_response_id`
- lease-based executor ownership
- the latest replay anchor (`latest_response_id`) used for fresh upstream reattach

The existing in-memory bridge session remains the fast path, but correctness no longer depends on one pod retaining every alias in RAM.

## Data Model

### `http_bridge_sessions`

Stores one row per canonical bridge continuity key.

- `session_key_kind`, `session_key_value`, `session_key_hash`
- `api_key_scope`
- `owner_instance_id`, `owner_epoch`, `lease_expires_at`
- `state`
- `account_id`, `model`, `service_tier`
- `latest_turn_state`, `latest_response_id`
- `last_seen_at`, `closed_at`

### `http_bridge_session_aliases`

Stores replayable aliases scoped by API key identity.

- `alias_kind`
- `alias_value`, `alias_hash`
- `session_id`
- `api_key_scope`

## Ownership / Recovery Rules

1. Ring membership still computes a preferred owner in steady state.
2. Durable lookup runs before fail-closed continuity errors.
3. If a durable row exists, the request is canonicalized to that session key.
4. If the durable row has a live owner lease on another replica, owner-forward remains the preferred fast path.
5. If the durable row has `latest_response_id` and the request does not provide `previous_response_id`, the service injects that replay anchor before preparing the new upstream request.
6. When owner-forward fails or the live in-memory alias is missing, the service may create a fresh upstream websocket locally and continue from the durable replay anchor.

## Shutdown / Drain

Before closing in-memory bridge sessions, the process marks durable rows owned by the instance as `draining`. When sessions are closed, the durable lease is released without deleting the continuity row or alias history.

## Remaining Gap

This change targets zero-drop continuity for the **next turn** after owner restart or handoff failure.

It does **not** provide crash-safe migration of an already open streaming response from one process to another. A mid-stream pod crash can still interrupt the active response stream; the durable journal only guarantees that subsequent valid turns can recover via fresh upstream reattach.
