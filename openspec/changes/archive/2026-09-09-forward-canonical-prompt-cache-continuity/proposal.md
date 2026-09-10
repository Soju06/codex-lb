# Forward hard continuity retained on canonical prompt-cache bridges

## Why

An HTTP Responses bridge can begin with a soft prompt-cache key and later
publish a turn-state and previous-response alias. Durable lookup intentionally
preserves the original prompt-cache key as the canonical bridge identity.
When a continuation lands on another replica, owner routing currently looks
only at that canonical key's soft strength, attempts a local rebind, loses the
durable claim to the live owner, and surfaces `bridge_instance_mismatch` to the
client instead of using the existing internal owner-forward transport.

## What Changes

- Treat an incoming turn-state or previous-response reference as hard bridge
  continuity when choosing between remote-owner forwarding and soft local
  prompt-cache rebinding, even when durable lookup retains a canonical
  prompt-cache key.
- Preserve prompt-cache-only requests as soft locality and preserve the
  existing explicit recovery-rebind exceptions.
- Add service-level and `/v1/responses` regression coverage for the
  cross-replica continuation path reported in #2035.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: hard continuation evidence overrides the soft
  strength of a retained canonical prompt-cache bridge for replica ownership.

## Impact

- Affected code: HTTP bridge remote-owner selection in
  `app/modules/proxy/_service/http_bridge/mixin.py`.
- Affected behavior: a continuation received by a non-owner replica is
  forwarded internally rather than failing after a local durable claim.
- No setting, schema migration, dependency, endpoint, or dashboard change is
  introduced.
