## ADDED Requirements

### Requirement: A durably unroutable bridge continuity owner is retired

A durable HTTP bridge session whose owner account has stayed unroutable across the hard-owner
grace window MUST have that ownership retired rather than kept. An owner counts as unroutable
when its status is one of `paused`, `deactivated`, `rate_limited`, `quota_exceeded` or
`reauth_required` **and** it carries no reset horizon still in the future. The grace window is
the same one the sticky hard-owner sweep uses, and it is measured from the session's own last
successful activity: what it protects is a **live thread**, not a young outage. A session that
served a turn inside the window MUST keep its owner however long that owner has been
unroutable; a session that has served nothing across the window MAY be retired as soon as its
owner is unroutable, however recently that became true. Retirement MUST be recorded as a
marker on the row, never as a deletion: a deleted row is indistinguishable from a key that was
never seen, which leaves the anchored path failing closed even after the owner recovers.
Retirement MUST NOT delete, detach, or otherwise disturb the session's operation records, and
MUST NOT be conditioned on their absence.

#### Scenario: Owner unroutable across the whole window

- **GIVEN** a bridge session whose owner account is paused, re-authentication-blocked, or past
  its reset horizon
- **AND** the session has had no successful activity for the grace window
- **WHEN** the cleanup sweep runs
- **THEN** the session's continuity ownership is retired
- **AND** the session row and every operation record it owns still exist

#### Scenario: A live thread survives a transient outage

- **GIVEN** a bridge session that served a turn inside the grace window, or whose owner is rate
  limited with a reset horizon still in the future
- **WHEN** the cleanup sweep runs
- **THEN** the session keeps its owner
- **AND** a later request still resolves that owner as the thread's account

#### Scenario: A dormant thread is retired on a fresh outage

- **GIVEN** a bridge session that has served nothing across the grace window
- **WHEN** its owner becomes unroutable and the cleanup sweep runs
- **THEN** the session's continuity ownership is retired, even though the outage is new
- **AND** the next resume starts fresh on a healthy account instead of failing closed

#### Scenario: Healthy owner is untouched

- **WHEN** the cleanup sweep runs against a session whose owner is active, however long the
  session has been idle
- **THEN** the session keeps its owner

### Requirement: A retired owner yields a fresh start, not a fail-closed

A lookup that resolves a retired session MUST report no owner and no continuity evidence — no
response anchor, no turn state, no pending tool calls — while still identifying the session, so
a request can claim the row for a new owner. Continuity checks that fail closed on a missing
owner MUST NOT fail closed on a retired one: the marker is positive proof the owner was
deliberately abandoned, so selecting a replacement is authorized. Retirement MUST be reversible:
a successful claim MUST clear it, restoring ordinary hard ownership.

#### Scenario: Resuming a thread whose owner was retired

- **GIVEN** an existing bridged thread whose owner was retired
- **AND** at least one healthy account is available
- **WHEN** the client resumes that thread
- **THEN** the request is served on a healthy account instead of failing closed
- **AND** the retired account is not selected for it

#### Scenario: Retired ownership is re-established

- **WHEN** a request claims a retired session for a new owner
- **THEN** the retirement marker is cleared
- **AND** a later lookup reports the new owner as ordinary hard ownership

#### Scenario: A genuinely missing owner still fails closed

- **GIVEN** a session that should name an account but carries no owner and no retirement marker
- **WHEN** a hard continuation resolves it
- **THEN** the request still fails closed, because lost state is not abandoned state

### Requirement: An expired retirement marker is collected

A retired session that stays unclaimed for a further full grace window MUST be deleted, and its
aliases with it, provided it owns no operation records. A retired session that still owns
operation records MUST be kept, because deleting it would cascade the durable recovery ledger.

#### Scenario: Unclaimed tombstone without a ledger

- **GIVEN** a retired session with no operation records whose marker is older than the grace
  window
- **WHEN** the cleanup sweep runs
- **THEN** the session and its aliases are deleted

#### Scenario: Unclaimed tombstone with a ledger

- **GIVEN** the same session, but owning at least one operation record
- **WHEN** the cleanup sweep runs
- **THEN** the session is kept
