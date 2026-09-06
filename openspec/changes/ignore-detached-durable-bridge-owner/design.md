## Context

`AccountsRepository._close_http_bridge_sessions_for_account` (called on `DEACTIVATED`/`REAUTH_REQUIRED` status writes, proxy-binding changes and `begin_delete`) detaches durable rows in place: `account_id`, `owner_instance_id`, `lease_expires_at`, `latest_turn_state`, `latest_response_id`, input fingerprint and pending tool calls are cleared, state becomes `CLOSED`, and aliases are deleted. Rows are kept because they may still own operation transcripts, and `purge_closed_before` skips rows with operations.

`DurableBridgeSessionCoordinator.lookup_request_targets` resolves aliases first and then falls back to `repository.get_session` by canonical key, which has no state filter. `find_session_by_latest_turn_state` / `find_session_by_latest_response_id` already restrict themselves to `ACTIVE`/`DRAINING` rows. The HTTP-bridge streaming path treats any durable hit for a hard key with `account_id is None` as `durable_owner_missing` and fails closed.

## Goals / Non-Goals

**Goals:**

- A detached row is invisible to durable request-target lookup so the request follows the same path as a thread with no durable row.
- Keep the fail-closed behavior for every row that still identifies an owner or carries continuity anchors.
- Keep the change at the single lookup that produces the durable evidence, so every caller (initial lookup and fresh turn-state re-lookup) benefits.

**Non-Goals:**

- Change how or when rows are detached, or delete detached rows eagerly.
- Change previous-response owner resolution through request logs, live bridge ownership, or file pins.
- Change the trigger that deactivated accounts on a bare upstream 404 (handled separately in the usage-refresh policy change).

## Decisions

### Filter detached rows in the coordinator, not in the streaming owner check

The streaming code has several consumers of `durable_lookup` (preferred owner, `durable_lookup_requires_owner`, model-transition owner, dead-owner anchor recovery). Returning `None` from the coordinator for detached rows makes all of them behave exactly as for a thread without durable state, which is the semantics the account-invalidation path intended. A predicate that is deliberately narrow (`CLOSED` + no `account_id` + no `owner_instance_id` + no `latest_turn_state` + no `latest_response_id`) matches only the detach shape and the ON DELETE SET NULL shape of an already-closed row.

Alternative considered: filter `CLOSED` rows out of `get_session`. Rejected because an ordinary release keeps the owner account and its anchors on a `CLOSED` row and the continuation logic relies on that evidence.

### Reclaim reuses the detached row

`claim_session` already treats an existing `CLOSED` row as claimable, so the fresh selection re-owns the same canonical row instead of creating a duplicate key.
