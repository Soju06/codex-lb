## Why

Blue-green deploys keep the retiring codex-lb backend alive while the router
points new requests at the replacement. During that overlap, durable HTTP
bridge sessions can still be owned by the retiring backend. If the replacement
cannot resolve the retiring owner endpoint, it falls through to a local durable
claim and returns `409 bridge_instance_mismatch` to the Codex client.

## What Changes

- Resolve endpointless active ring members through their bridge instance id as
  an internal `http://<instance-id>:2455` endpoint.
- Keep explicit advertised endpoint metadata authoritative when present.
- Preserve fail-closed behavior for malformed instance ids and unreachable
  owner relays.

## Impact

- Rolling single-host blue-green deploys can use the existing owner-forwarding
  path instead of surfacing raw 409 conflicts.
- No new setting, schema change, or deploy-helper live mutation is required.
