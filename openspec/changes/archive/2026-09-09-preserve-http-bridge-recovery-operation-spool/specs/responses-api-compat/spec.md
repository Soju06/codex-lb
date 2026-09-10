## ADDED Requirements

### Requirement: Optional recovery-spool cleanup MUST remain optional

For an anchored local HTTP bridge recovery that has not dispatched its
replacement request, the implementation MUST attempt the operation-spool
cleanup while the failed durable session still holds the operation owner fence.
If that cleanup is unavailable, refuses the owner, or raises, the optional
cleanup MUST NOT replace the original recovery path with a
`bridge_continuity_persistence_failed` response. The existing durable operation
rebind remains responsible for clearing stale attempt material before the
replacement dispatch.

#### Scenario: Optional reset refusal does not abort local recovery

- **GIVEN** an anchored operation belongs to the failed durable session
- **AND** local recovery has not dispatched a replacement request
- **WHEN** the optional spool reset returns `False`
- **THEN** recovery continues with the same operation identity
- **AND** the replacement is allowed to reach the normal fenced operation
  rebind path.

#### Scenario: Optional reset exception does not mask the upstream failure

- **GIVEN** the same anchored local recovery path
- **WHEN** the optional spool reset raises
- **THEN** the exception is handled as optional cleanup failure
- **AND** recovery does not emit a new continuity-persistence error solely for
  that cleanup failure.

### Requirement: Required replay-spool cleanup MUST fail closed

Before an account-neutral or owner-bound unanchored stale-anchor replay, the
implementation MUST keep the operation fence and required spool reset strict.
An unavailable or refused required reset, or a required reset operation that
raises an exception, MUST return the typed `bridge_continuity_persistence_failed`
error and MUST NOT dispatch the unanchored replacement request.

#### Scenario: Required reset refusal blocks unanchored replay

- **GIVEN** a verified stale-anchor full-resend replay
- **WHEN** the required operation-spool reset is unavailable or returns
  `False`
- **THEN** the request fails with `bridge_continuity_persistence_failed`
- **AND** no unanchored replacement request is sent.
