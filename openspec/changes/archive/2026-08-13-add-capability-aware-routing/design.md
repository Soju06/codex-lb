## Context

Current `main` already exposes the operator-controlled
`Account.security_work_authorized` grant, filters the canonical load balancer
when `require_security_work_authorized=True`, propagates that flag through
direct-WebSocket retry state, and reports the typed empty-pool error. It does
not have a trusted proactive signal or durable state that can restore the flag
after a new downstream connection omits the generated turn state.

Existing persistence cannot safely stand in for that state. A sticky row means
account ownership rather than capability; inferring from a capable owner would
mark ordinary work and loses the requirement when ownership changes. Durable
HTTP-bridge rows do not cover direct WebSockets and are retention-bound.
Request logs are also retention-bound and contain no routing requirement.

## Goals / Non-Goals

**Goals:**

- Establish authenticated `trusted_cyber` intent before the first direct
  Responses WebSocket account selection.
- Persist a monotonic, API-key-scoped requirement before upstream dispatch and
  restore it on reconnect with or without a client-echoed turn state.
- Reuse the canonical account selector, retry, ownership, admission, and
  `security_work_authorized` contracts already on `main`.
- Keep raw lineage identifiers out of the new capability-marker persistence
  and capability-specific observability, and keep the internal capability
  carrier out of upstream traffic, archives, and logs.
- Add only the schema needed for this durable requirement.

**Non-Goals:**

- A new account grant, routing strategy, prompt classifier, reservation policy,
  reactive `cyber_policy` retry, or generic capability framework.
- HTTP/compact/HTTP-bridge capability ingress in this first direct-WebSocket
  slice.
- Dashboard, settings, README, or production/deployment changes.
- Historical backfill from draft-only lineage representations.

## Decisions

### Use one exact authenticated, narrowing-only signal

The accepted value is `trusted_cyber` under the exact internal key
`X-Codex-LB-Required-Capability`. A direct WebSocket may carry it on the
handshake or in the current `response.create.client_metadata`, because the
decision can be made after opening the downstream socket. The parser accepts
exactly one carrier only after existing proxy API-key authentication. Unknown,
malformed, duplicate, conflicting, or unauthenticated signals fail before
selection. Capability-aware raw JSON parsing retains duplicate carrier keys
long enough to reject them instead of accepting last-key-wins normalization;
the reserved metadata key is also rejected on non-`response.create` frames.

The signal grants nothing. It only turns on the existing
`require_security_work_authorized` selector constraint inside the caller's
already-authorized account/model/ownership pool.

Alternative: infer cyber intent from prompts, user-agent, or multi-agent
headers. Those values are not authenticated authorization inputs and are
rejected.

### Store opaque capability-lineage markers in a dedicated table

`capability_lineage_markers` stores a single SHA-256 marker key plus creation
and last-seen timestamps. The digest covers a versioned domain, capability,
API-key scope, lineage kind, and normalized lineage value. No raw lineage,
account identifier, or user payload is stored.

Repository writes use an atomic insert/upsert and can only establish or refresh
a marker; routing code never clears it. Lookup and write accept explicit typed
lineage aliases. Reusing the existing proxy repository session keeps writes
transactionally simple while the dedicated table avoids changing sticky
ownership, nullable account foreign keys, account deletion, HTTP-bridge
cleanup, or usage history.

Alternative: encode detached marker rows in `sticky_sessions`. That requires a
nullable account, different foreign-key deletion behavior, special purge rules,
and account lifecycle changes. A dedicated table is smaller and less coupled.

### Restore from stable direct-WebSocket lineage before selection

The routing seam considers authenticated session identity, accepted or
synthesized turn state, `previous_response_id`, and domain-separated Codex
window/parent identifiers. Externally supplied identifiers cannot impersonate
marker keys because the repository hashes their explicit lineage kind.

An explicit trusted signal first persists every known alias, then requires the
capable pool. A later request first checks its lookup aliases; inherited
REQUIRED state is persisted onto any newly generated alias before selection.
The accepted session identity remains a lookup alias when the proxy synthesizes
a turn state, which is what makes no-echo reconnect safe.

If a later frame establishes REQUIRED while an idle upstream socket belongs to
an ordinary account, the proxy retires that socket and reselects before send.
If a prior frame is still pending, it fails closed rather than mixing account
requirements on one upstream connection.

The socket contract is the constraint used when that socket was selected, not
the selected account object's capability snapshot. A REQUIRED-selected socket
is also revalidated through the canonical selector before each later REQUIRED
frame so a revoked grant cannot be reused from stale in-memory state. Typed and
unexpected revalidation failures settle the frame reservation and fail closed
before send.

When `response.created` first reveals an upstream response ID for a durably
REQUIRED request, the proxy commits that previous-response alias before making
the created frame visible downstream. A failed propagation emits the typed
lineage-unavailable error, does not expose the unpersisted ID or penalize the
upstream account, and retires the socket without replaying accepted work.

### Fail closed on persistence uncertainty

The table is required for authenticated lineage lookup. A read or write error
produces a typed capability-lineage-unavailable response before ordinary
dispatch. An explicit signal with no reusable alias still constrains the
current in-memory request; it does not claim future inheritance.

### Treat the carrier as private proxy metadata

The handshake header is part of the proxy's internal-header denylist. The exact
metadata key is removed from the copied `response.create` payload before
upstream send and before request archival. Other client metadata is preserved.

## Risks / Trade-offs

- **Marker storage grows monotonically** -> rows are opaque and small; this
  slice favors never downgrading security lineage over unsafe TTL expiry.
- **A migration is required** -> the revision creates one empty table with no
  data rewrite and descends from the current single head.
- **An authenticated caller may self-select the narrow pool** -> it cannot add
  account/model access and can only reduce candidates.
- **A capable account later becomes unavailable** -> canonical selection fails
  with the existing typed no-capable-account error; it never widens the pool.

## Migration Plan

Create `capability_lineage_markers` after
`20260725_000000_add_http_bridge_pending_tool_calls`. Upgrade creates the empty
table and its primary-key index without scanning existing rows. Downgrade drops
only that table. Application rollback after upgrade is compatible because the
older code ignores the additive table.

## Open Questions

None for this direct-WebSocket slice.
