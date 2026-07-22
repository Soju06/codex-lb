## Why

An HTTP Responses follow-up can be restricted to a continuity owner that is no longer selectable while other accounts remain healthy. Treating that restricted miss as global `503/no_accounts` both misreports pool health and prevents a proof-gated full resend from recovering on another account. Recovery also needs durable task ownership: without a server-only marker and atomic alias fencing, a stale session, replica restart, or model transition can send the recovered task back to the retired account and recreate encrypted-item ownership failures.

## What Changes

- Carry explicit ownership provenance into account selection and return typed continuity-owner availability or policy-conflict codes without marking a healthy wider pool degraded.
- Map only the typed selection-time owner-availability result to HTTP bridge `previous_response_owner_unavailable`; preserve policy, capacity, authentication, connection, and other failures.
- Permit a fresh cross-account replay only for a durable count-and-fingerprint-verified full resend whose deterministic plaintext projection is account-neutral and self-contained. Remove owner-bound reasoning, upstream item identities, and completed search bookkeeping before validation; shape-validate retained internal turn metadata; then remove the previous-response anchor and stale session aliases, exclude the failed owner, and create a local server-namespaced recovery lane.
- Persist the recovery lane across task aliases, reconnects, model transitions, and fresh-process startup. Use atomic durable alias fencing so a stale owner cannot reclaim a recovered alias and a protected sibling alias does not evict the whole local session.
- Add unit, integration, architecture, and specification coverage for the selection, replay, forwarding, persistence, restart, and next-turn paths.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: Define typed required-owner selection and global-health ownership provenance.
- `responses-api-compat`: Define verified account-neutral full-resend recovery and durable task-specific continuity.

## Impact

- Affected code: account selection, HTTP bridge streaming/session lifecycle, internal owner forwarding, and durable bridge alias persistence.
- Affected API behavior: a verified full resend can recover after selection-time owner loss; unsafe continuations continue returning `previous_response_owner_unavailable`.
- No database schema, migration, setting, dependency, dashboard, or public request field is added.
