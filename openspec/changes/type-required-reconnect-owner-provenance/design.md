## Context

`_reconnect_http_bridge_session` resolves live file pins,
`require_preferred_account`, and account-neutral replay into a required
preferred account. Required ownership already disables fallback, rejects owner
substitution, and maps every terminal miss to the same external 502. On main,
non-file require-preferred owners are intentionally ordinary required accounts:
they bypass dashboard single-account narrowing and remain subject to API-key
assignment scope.

Only account-neutral replay currently carries continuity-owner provenance. A
file-pin owner therefore cannot produce the selection layer's confirmed
"account no longer exists" classification. Typing every required owner would
also change non-file single-account and assignment-scope eligibility, while
mapping every typed unavailable result immediately would remove transient
capacity recovery.

## Goals / Non-Goals

**Goals:**

- Type a live file-pin reconnect owner sufficiently to recognize confirmed
  account disappearance.
- Return the existing required-owner 502 immediately only for that confirmed
  disappearance.
- Preserve bounded sleep/retry for transient owner saturation.
- Preserve main's non-file require-preferred single-account and API-key scope
  semantics.

**Non-Goals:**

- A product-policy change for previous-response or other require-preferred
  owners.
- Create-path provenance, affinity or sticky writes, fallback policy, API-key
  security scope, or hard-session reconnect behavior.
- A new public error code or envelope.

## Decisions

### Scope new provenance to file-pin ownership

Reconnect sets `preferred_account_is_continuity_owner` for a live file pin or
the already-typed account-neutral replay path. A previous-response or other
`require_preferred_account` owner remains untyped. This preserves main's
existing service-layer behavior for those owners: required preferred selection
bypasses dashboard single-account narrowing and does not become eligible
outside API-key assignment scope.

### Preserve file-pin routing policy while adding provenance

Continuity typing normally enters single-account narrowing and may admit the
owner outside assignment scope. The file-pin-only override skips the
single-account branch, matching main's required-preferred behavior. That
override does not bypass assignment scope: an out-of-scope owner remains
ineligible before load-balancer selection.

### Distinguish confirmed disappearance from transient unavailability

`continuity_owner_unavailable` is also used for generic continuity misses.
Early reconnect mapping therefore checks the typed code together with the
selection-layer "Required continuity owner account no longer exists" reason.
Only `_required_continuity_owner_failure` emits that pair after checking the
runtime account catalog.

A `hard_affinity_saturated` miss remains `hard_affinity_saturated` instead of
being rewritten to `continuity_owner_unavailable`. The existing recovery helper
recognizes that transient code, waits within the reconnect deadline, and
retries the same required owner. Terminal misses still use
`_http_bridge_reconnect_selection_failure`, so the external fail-closed 502 is
unchanged.

## Risks / Trade-offs

- The genuine-disappearance discriminator includes the internal selection
  reason as well as its typed code. A shared predicate centralizes that pair so
  producers and consumers cannot drift independently.
- File-pin continuity still reaches existing continuity-owner policy-conflict
  handling when the owner is otherwise ineligible. The file-pin override is
  deliberately limited to preserving pre-existing single-account behavior and
  does not weaken assignment or security scope.

## Migration Plan

No migration. Reverting the file-pin provenance flag, scoped routing override,
and early confirmed-disappearance predicate restores main's prior delayed
terminal mapping.
