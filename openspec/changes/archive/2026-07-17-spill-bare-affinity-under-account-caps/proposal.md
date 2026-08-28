## Why

A process-level Codex session header is currently treated as an account-ownership constraint when its sticky account reaches a local response-create or stream cap. This can reject self-contained, pre-visible work even though another eligible account has capacity.

The earlier persistent-rebind prototype demonstrated that moving the mapping itself requires transport-specific settlement and distributed rollback. Request-local spillover provides the needed availability without changing durable ownership.

## What Changes

- Distinguish bare process-session locality from owner-bearing Codex continuity.
- Allow a self-contained, pre-visible request to select an eligible alternate when its bare-session account is locally capped.
- Keep the existing bare-session mapping unchanged during spillover; later requests return to it unless an ordinary hard owner signal routes elsewhere.
- Make resolved previous-response, file, conversation, turn-state, live/durable bridge, and replay ownership override the bare-session hint and fail closed when unavailable.
- Keep late admission races bounded: once transport ownership exists, return the existing local-cap error instead of switching a shared transport or publishing a replacement bridge.
- Enable the behavior by default without a setting, schema migration, or response-shape change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: Define request-local spillover for bare-session work under account caps and preserve bounded failure after transport handoff.
- `responses-api-compat`: Treat live file pins as hard ownership while preserving verbatim forwarding for opaque file IDs with no live pin.
- `sticky-session-operations`: Define bare session-header mappings as soft locality whose stored owner is not rewritten by account-cap spillover, while owner-bearing continuity remains hard.

## Impact

- Affinity-source classification and shared account-selection precedence in `app/modules/proxy/affinity.py`, `app/modules/proxy/service.py`, and `app/modules/proxy/load_balancer.py`.
- Existing Responses, compact, direct WebSocket, and HTTP bridge selection callers may propagate the source capability, but do not gain new publication, replay, or cleanup state.
- Focused load-balancer, service-boundary, and externally visible routing regression coverage.
- No database migration, setting, environment variable, dashboard, or API schema change.
