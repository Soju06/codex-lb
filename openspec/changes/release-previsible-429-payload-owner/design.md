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
  another account when ciphertext is the body's only account-scoped retained
  state and the rejected attempt is the only prospective dispatch owner.
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
classified as rate-limit or quota failures, but only when ciphertext is the
body's sole account-scoped retained state. The classifier removes encrypted
items only for analysis, rejects file IDs through the canonical extractor, and
then requires the remainder to pass the canonical account-neutral fresh-replay
predicate. File, container, vector-store, nonneutral URL, and unknown retained
state therefore stay owner-bound even when no durable owner can be resolved.
An owner established before the rejection also remains intact. Other retryable
errors, including stream idle timeouts, remain owner-establishing because their
dispatch outcome is ambiguous.

The routed regressions use compacted input and encrypted reasoning because both
are nonportable under the fresh-replay predicate and therefore enter pending
dispatch-owner registration. A plain text request would be account-neutral and
would not exercise the defect. The ciphertext cases assert exact forwarding on
the second account; a separate unresolved-file case proves that other retained
state remains on the first dispatch account.

Treat literal `invalid_encrypted_content` and the already-recognized observed
reasoning-decryption rejection shape as account-neutral request rejections.
They must not increment the target account's health error count: after the
proxy deliberately moves the ciphertext, that response is evidence about
request portability rather than the target account's availability. A dedicated
warning carries the source and target account provenance only when the
rejection follows the classified cross-account failover.

## Risks / Trade-offs

- [Risk] A limit body could be mistaken for accepted work. The exception is
  restricted to normalized rate-limit and quota classifications before any
  downstream-visible event; visible or ambiguous failures continue to
  establish or preserve ownership.
- [Risk] The exception could weaken file or continuation pinning. The
  ciphertext-only predicate also rejects unresolved file and other
  non-ciphertext account-scoped references before owner release; both durable
  and transient owner cases have fail-closed regressions.
- [Risk] Cross-account ciphertext submission may be observable upstream or
  cease to be accepted. Controlled probes show valid blobs accepted across
  distinct subscription identities and modified blobs rejected, but upstream
  does not document the cipher, account portability, or policy consequences.
  The exception remains limited to classified pre-visible quota/rate-limit
  rejection, and a future conservative opt-out can be considered separately.
  A dedicated warning identifies literal or observed encrypted-content
  rejection returned by the alternate account after this exact failover,
  including source and target account provenance but never ciphertext, so
  operators can detect a change in upstream portability without reconstructing
  raw request logs. That request-shaped rejection does not penalize the target
  account's health.

## Migration Plan

No migration is required. Rollback restores the prior owner-registration
condition.

## Open Questions

- Should a future release offer a conservative policy that fails closed instead
  of sending encrypted reasoning across subscription identities?
