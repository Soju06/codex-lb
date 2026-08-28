## Context

PR #1486 makes a fingerprint-verified complete resend, including an exact
response-bound pending-tool-call settlement, start a fresh upstream bridge
without the stale durable `previous_response_id`. Account selection still
receives the original session-header affinity, though. During rolling upgrades,
a raw legacy `CODEX_SESSION` row may represent older hard turn-state ownership
on account A while the more specific durable bridge row owns this task on
account B. The load balancer correctly refuses to choose between those sources,
but the resulting retryable error cannot converge because neither persisted row
changes.

## Goals / Non-Goals

**Goals:**

- Let a verified complete resend establish a fresh bridge on its durable owner.
- Remove only the broad session-header source that causes the deterministic
  selection conflict.
- Keep subsequent incremental continuity on the newly established bridge.
- Make the eligibility proof immutable, request-bound, and unavailable through
  ordinary construction or hydration.

**Non-Goals:**

- Prefer one conflicting turn-state, previous-response, file, or other specific
  owner over another.
- Add or widen an account-movement path. Existing separately proved
  account-neutral replay after a genuine owner-unavailable result remains
  unchanged.
- Delete or rebind the broad legacy row, which may still own sibling work.
- Retry anything after upstream dispatch may have started.

## Decisions

### 1. Bind the bypass to a sealed local proof

The verifier requires a durable owner, latest response ID, positive stored input
count, full fingerprint, exact raw-prefix match, and either retained prior
assistant output followed by fresh input or an exact call/output settlement of
the response-bound pending-tool-call manifest. The proof records the durable
session, owner, response, stored metadata, pending-tool-call manifest identity,
and full input fingerprint. Its normal constructor is sealed inside the
verifier closure, its fields are immutable, and serialization is rejected.
Before use, it is matched again against the current payload and durable lookup
so mutation or state substitution invalidates the proof.

The proof is request-local. It is never accepted from a caller, serialized,
persisted, cloned into another request, or hydrated from the database.

### 2. Remove only broad legacy selection provenance

When the proved full resend is about to create a fresh owner-bound bridge from a
session header, the service removes downstream session and turn aliases from
the new upstream connection and replaces selection affinity with a
`CODEX_SESSION` policy that has no client key or legacy source. The durable
canonical bridge key and durable owner account remain unchanged, so selection
cannot move to another account.

The broad sticky row is left intact for sibling traffic. Specific durable
turn-state, previous-response, and file-owner checks occur before this step and
remain hard conflicts.

This reconciliation does not itself rebind the request to another account.
Existing account-neutral full-resend recovery after a genuine
owner-unavailable result remains separately gated by its own projection and
replay-safety checks.

### 3. Preserve Codex bridge semantics after creation

The selection policy retains `CODEX_SESSION` kind even though it drops the
stale client key. The created session therefore remains a Codex continuity
session and can inject the response ID established by the successful fresh
request for later incremental turns.

## Risks / Trade-offs

- The new upstream connection no longer receives the stale session header on
  this one recovery path. The durable canonical key still owns internal routing,
  and the complete request supplies the upstream context.
- Python cannot prevent hostile reflection through `object.__new__`, but
  ordinary construction, mutation, copying with altered fields, and
  serialization are closed; every use also revalidates the payload and durable
  identity.
- An incomplete resend may still surface a continuity conflict. It remains
  fail-closed because dropping either owner would risk context or account-bound
  state.

## Example

Durable session `S` records owner B, response `resp_old`, two stored input items,
their fingerprint, and any pending tool calls bound to that response. A raw
legacy sticky row for the shared session header still points at A. The client
resends the two stored items plus either retained completed assistant output and
a new user message or the exact pending call/output settlement. codex-lb proves
the complete resend, opens the fresh bridge on B without `resp_old`, and omits
the stale broad header from selection and the upstream handshake. The raw row
remains on A for unrelated sibling traffic.
