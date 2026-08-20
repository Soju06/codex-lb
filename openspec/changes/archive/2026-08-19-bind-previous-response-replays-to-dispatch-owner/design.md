## Context

Responses requests can carry both a server-side continuation anchor and
client-retained material. Removing a stale `previous_response_id` does not make
the remaining body portable: encrypted reasoning, account-scoped probe items,
and other retained state can still belong to the account that first received
the request.

The selector already supports strict required-account routing for known
previous-response and file owners. The missing state is payload dispatch
provenance: after a pre-visible retry excludes an account, later selection can
no longer tell that the retained body was already dispatched there.

## Goals / Non-Goals

**Goals:**

- Bind nonportable payloads to their first dispatch account.
- Enforce the binding consistently in HTTP streaming, HTTP bridge, and direct
  WebSocket retry paths.
- Allow cross-account replay only after exact-wire verification proves the
  resulting request is an account-neutral fresh replay.
- Preserve existing settlement, health-write, and file-owner invariants.

**Non-Goals:**

- Changing stale previous-response error classification from PR #1818.
- Preserving or reshaping unrelated bare/raw upstream error fields.
- Changing public API envelopes, retry counts, quota accounting, or settings.
- Making encrypted reasoning or account-scoped probe items portable.

## Decisions

### Use the canonical portability predicate

Every candidate body is evaluated with
`responses_payload_is_account_neutral_fresh_replay`. Ad hoc checks for files or
`previous_response_id` are insufficient because account scope can live in
retained input items.

Alternative: extend each transport's file checks. Rejected because it
duplicates an incomplete allowlist and already failed to catch encrypted
reasoning.

### Bind on first nonportable dispatch

A request-local dispatch-owner ID is authorized before the first nonportable
payload is sent and persisted after the first upstream event or normal stream
completion. Ambiguous/post-dispatch failures also preserve that owner, while a
positively confirmed pre-dispatch transport failure does not create one. Every
later selection treats a persisted owner like any other strict continuity
requirement.

Alternative: infer ownership from the current preferred account. Rejected
because retry branches intentionally clear or replace preference state.

### Clear ownership only after verified neutral replay

Verified stale-anchor recovery may replace the wire body with a reconstructed
fresh request. The dispatch binding is cleared only when that exact replacement
passes the canonical account-neutral predicate. Body replacement and
owner-fence clearing occur in one transition so a retry cannot observe mixed
state. A verified nonneutral replacement may be installed for a same-owner
retry, but that transition preserves the existing dispatch-owner fence.

Alternative: clear ownership whenever the anchor is removed. Rejected because
the reproduced defect retained owner-bound ciphertext after anchor removal.

### Treat proxy-owned operation metadata as account-bound

HTTP bridge sends may add `codex_lb_operation_id` after request preparation.
Until a dedicated rebind path replaces that operation identity, selection
treats the request as nonportable and requires the current account.

Alternative: remove the proxy-owned field before portability checks. Rejected
because normalization would authorize a different account while preserving the
same operation identity on the final wire request.

### Fail closed across transport-specific recovery

Trusted Access migration/degradation, owner exclusion, bridge reconnect, and
WebSocket account switching may not bypass payload ownership. If the owner
cannot satisfy the retry, the proxy returns the stable owner-unavailable error
without dispatching the retained body elsewhere.

A generic authentication failure is split into two decisions: one forced token
refresh may replay a bound body on the same owner, while owner exclusion or
cross-account migration still requires an atomically installed neutral body.
Permanent authentication failure remains terminal for a bound body.

## Risks / Trade-offs

- **Fewer automatic retries for account-bound bodies** → This is intentional;
  confidentiality and continuation correctness outrank cross-account fallback.
- **False nonportability** → The canonical predicate is an explicit allowlist,
  so unknown retained item types fail closed.
- **Transport drift** → Shared helpers plus focused HTTP, bridge, and WebSocket
  regressions keep the invariant aligned.
- **Settlement regression** → The change does not move reservation settlement
  or deferred health writes; existing settlement tests remain mandatory.

## Migration Plan

No data or configuration migration is required. Deploy the proxy code normally.
Rollback is a code rollback; no persisted format changes.

## Open Questions

None. Current-main runtime probes reproduce the cross-account dispatch and the
existing selector already provides the strict owner-routing primitive.
