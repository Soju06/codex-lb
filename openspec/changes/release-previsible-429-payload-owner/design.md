## Context

For a nonportable request body, the streaming path delays establishing a
transient dispatch owner until the upstream iterator produces output, exits,
or raises. Most ambiguous exceptions establish the owner because upstream may
have accepted account-scoped state. Confirmed pre-dispatch transport failures
are already exempt because no upstream bytes were sent.

A classified rate-limit or quota rejection is different from an ambiguous
transport failure. It is a definitive upstream rejection, and the existing
retry classifier intentionally excludes the limited account and chooses
`failover_next`. The rejection can be raised from an HTTP 429 status or from a
`response.failed` event before the first downstream-visible line. Recording a
new owner for that rejected attempt contradicts the classified retry action.

## Goals / Non-Goals

**Goals:**

- Allow the existing pre-visible rate-limit and quota failover policy to select
  another account when the rejected attempt is the only prospective dispatch
  owner.
- Keep all independently resolved hard ownership constraints fail-closed.
- Prove the behavior through the external Responses route and real load
  balancer selection.

**Non-Goals:**

- Change prompt-cache stickiness or `reallocate_sticky` behavior.
- Move a live file, previous response, turn state, or existing dispatch owner
  across accounts.
- Retry after any downstream-visible response event.

## Decisions

Extend the existing exception around transient owner registration to both HTTP
429 and `_RetryableStreamError` values whose normalized codes are already
classified as rate-limit or quota failures. The exception applies only while
owner registration is still pending, so an owner established before the
rejection remains intact. Hard owners are resolved separately before dispatch
and remain required during selection. Other retryable errors, including stream
idle timeouts, remain owner-establishing because their dispatch outcome is
ambiguous.

The routed regressions use compacted input and encrypted reasoning because both
are nonportable under the fresh-replay predicate and therefore enter pending
dispatch-owner registration. A plain text request would be account-neutral and
would not exercise the defect. The encrypted-reasoning case also asserts that
the proxy forwards the exact ciphertext unchanged on the second account.

## Risks / Trade-offs

- [Risk] A limit body could be mistaken for accepted work. The exception is
  restricted to normalized rate-limit and quota classifications before any
  downstream-visible event; visible or ambiguous failures continue to
  establish or preserve ownership.
- [Risk] The exception could weaken file or continuation pinning. Those owners
  are established independently of transient dispatch registration and remain
  covered by existing fail-closed tests.
- [Risk] Cross-account ciphertext submission may be observable upstream or
  cease to be accepted. Controlled probes show valid blobs accepted across
  distinct subscription identities and modified blobs rejected, but upstream
  does not document the cipher, account portability, or policy consequences.
  The exception remains limited to classified pre-visible quota/rate-limit
  rejection, and a future conservative opt-out can be considered separately.

## Migration Plan

No migration is required. Rollback restores the prior owner-registration
condition.

## Open Questions

- Should a future release offer a conservative policy that fails closed instead
  of sending encrypted reasoning across subscription identities?
