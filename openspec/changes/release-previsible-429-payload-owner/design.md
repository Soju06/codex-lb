## Context

For a nonportable request body, the streaming path delays establishing a
transient dispatch owner until the upstream iterator produces output, exits,
or raises. Most ambiguous exceptions establish the owner because upstream may
have accepted account-scoped state. Confirmed pre-dispatch transport failures
are already exempt because no upstream bytes were sent.

HTTP 429 is different from an ambiguous transport failure. It is a definitive
upstream rejection, produces no response lifecycle event, and the existing
retry classifier intentionally excludes the limited account and chooses
`failover_next`. Recording a new owner for that rejected attempt contradicts
the classified retry action.

## Goals / Non-Goals

**Goals:**

- Allow the existing pre-visible 429 failover policy to select another account
  when the rejected attempt is the only prospective dispatch owner.
- Keep all independently resolved hard ownership constraints fail-closed.
- Prove the behavior through the external Responses route and real load
  balancer selection.

**Non-Goals:**

- Change prompt-cache stickiness or `reallocate_sticky` behavior.
- Move a live file, previous response, turn state, or existing dispatch owner
  across accounts.
- Retry after any downstream-visible response event.

## Decisions

Extend the existing exception around transient owner registration to HTTP 429.
The exception applies only while owner registration is still pending, so an
owner established before the rejection remains intact. Hard owners are
resolved separately before dispatch and remain required during selection.

The routed regression uses compacted input because it is nonportable under the
fresh-replay predicate and therefore enters pending dispatch-owner
registration. A plain text request would be account-neutral and would not
exercise the defect.

## Risks / Trade-offs

- [Risk] A 429 body could be mistaken for accepted work. The upstream response
  is a terminal HTTP rejection before any Responses event; visible or ambiguous
  failures continue to establish or preserve ownership.
- [Risk] The exception could weaken file or continuation pinning. Those owners
  are established independently of transient dispatch registration and remain
  covered by existing fail-closed tests.

## Migration Plan

No migration is required. Rollback restores the prior owner-registration
condition.

## Open Questions

None.
