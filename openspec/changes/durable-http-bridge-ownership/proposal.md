## Why

HTTP bridge turn-state continuity currently depends on an in-memory alias map. When a process restarts, evicts a session, or receives a replayed request on another replica, the proxy can only tell that the local alias is missing. It currently reports that as `bridge_instance_mismatch`, which conflates stale local state, expired bridge sessions, invalid turn-state tokens, and true live-owner conflicts.

## What Changes

- Replace opaque local-only HTTP bridge turn-state aliases with signed, versioned turn-state tokens.
- Add a durable HTTP bridge lease registry so replicas can distinguish a live owner mismatch from expired or stale bridge state.
- Recover stale or expired bridge turn-state on requests that do not require prior-response continuity.
- Fail with specific bridge error codes for invalid tokens, expired continuity, and true wrong-instance conflicts.

## Impact

- Code: `app/modules/proxy/service.py`, `app/modules/proxy/api.py`, `app/modules/proxy/repo_bundle.py`, `app/dependencies.py`, `app/db/models.py`
- Data: new `http_bridge_leases` table and migration
- Tests: HTTP bridge integration coverage and proxy repository factory call sites
- Specs: `openspec/specs/responses-api-compat/spec.md`, `context.md`, and `ops.md`
