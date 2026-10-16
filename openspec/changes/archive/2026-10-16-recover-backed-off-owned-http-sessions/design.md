## Context

See proposal.md. A required owner narrows selection to one account. The existing
backoff fallback needs another candidate or a persistently blocked sibling, so
neither condition can hold in that narrowed pool. A denied proxy anchor can
also clear the bridge's prefix metadata, making cross-account replay unsafe.

## Goals / Non-Goals

Recover a client retry on its ACTIVE owner without changing its conversation
history. Do not rebuild missing history, move encrypted context across accounts,
retry visible output, ignore real quota exhaustion, or change approval policy.

## Decisions

- Reuse #2486's explicit hard-owner-pool capability across core, sticky,
  pinned-retention, and unbound selection. Keep ordinary soft preferences and
  persisted owner unavailability on their established paths.
- Admit the owner through the existing bounded backoff fallback, without
  clearing counters merely for selection. Do not add a second bridge-specific
  retry loop or a setting.
- Exercise actual account selection and health recording at both HTTP routes
  with an authenticated API key and stubbed upstream sockets. Deny an injected
  anchor, verify its metadata was cleared, record transient owner errors, show
  a sibling thread succeeds, and then resume the original thread on its owner.
- Include opaque reasoning and a completed tool-call/output pair in the full
  resend. Verify exact forwarding and no replacement-account receipt; verify
  subsequent turn-state continuation and persistent quota refusal.

## Risks / Trade-offs

- An owner can still return a genuine upstream error. Existing request budgets
  and retry limits apply; selection does not assert that upstream will succeed.
- The fallback bypasses only transient backoff for hard ownership. Existing
  account status, security, scope, file, cap, and explicit-exclusion gates remain.
- Lost context is not made portable. A client must provide its own complete
  history; this change neither invents tool outputs nor migrates account state.

## Integration

The production algorithm is shared with the open #2486. Attribute that work
explicitly; if it merges first, rebase and retain only the new contract and
route regressions. Keep this PR separate from quota-reset metadata PR #2626.
