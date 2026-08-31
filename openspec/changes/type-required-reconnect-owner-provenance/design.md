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
After checking the runtime account catalog, the selection layer therefore sets
`continuity_owner_no_longer_exists` only for confirmed disappearance. Early
reconnect mapping checks that typed provenance rather than the broader error
code.

A `hard_affinity_saturated` miss remains `hard_affinity_saturated` instead of
being rewritten to `continuity_owner_unavailable`. The existing recovery helper
recognizes that transient code, waits within the reconnect deadline, and
retries the same required owner. Terminal misses still use
`_http_bridge_reconnect_selection_failure`, so the external fail-closed 502 is
unchanged.

## Risks / Trade-offs

- `AccountSelection` carries one boolean discriminator so reconnect does not
  infer confirmed disappearance from a broader error code or message.
- File-pin continuity still reaches existing continuity-owner policy-conflict
  handling when the owner is otherwise ineligible. The file-pin override is
  deliberately limited to preserving pre-existing single-account behavior and
  does not weaken assignment or security scope.

## Migration Plan

No migration. Reverting the file-pin provenance flag, scoped routing override,
and early confirmed-disappearance discriminator restores main's prior delayed
terminal mapping.
