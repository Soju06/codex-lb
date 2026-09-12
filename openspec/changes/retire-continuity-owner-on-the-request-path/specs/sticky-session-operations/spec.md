## ADDED Requirements

### Requirement: A continuity owner that cannot return in time is retired for the waiting turn

When a bridge turn fails because its continuity owner is unavailable, the proxy MUST decide
whether that owner can return before the turn's own request budget expires, and retire it when
it cannot. An owner whose reset horizon falls before the deadline MUST be waited for, preserving
the upstream conversation state. An owner whose reset horizon falls after the deadline, or which
carries no horizon at all, MUST be retired. The decision MUST be taken at most once per request,
MUST be serialized against a concurrent recovery of the same account, and MUST re-check the
owner's status inside the write so a prior observation alone can never authorize it.

#### Scenario: Owner with no recovery horizon

- **GIVEN** a bridged thread whose owner account is paused, re-authentication-blocked, or
  deactivated
- **WHEN** the turn fails because that owner is unavailable
- **THEN** the owner is retired and the turn is served on a healthy account within the same
  request
- **AND** no grace window has to elapse and no scheduled sweep has to run

#### Scenario: Owner returning inside the budget

- **GIVEN** a bridged thread whose owner is rate limited with a reset horizon before the
  request's deadline
- **WHEN** the turn fails because that owner is unavailable
- **THEN** the owner is not retired
- **AND** the thread keeps its owner so the upstream conversation state is preserved

#### Scenario: Owner returning after the budget

- **GIVEN** the same thread, but with a reset horizon after the request's deadline
- **THEN** the owner is retired rather than waited for

#### Scenario: A healthy owner is never retired

- **WHEN** the owner's status is not one of the unavailable statuses at the moment of the write
- **THEN** no retirement is recorded, whatever the failure was

#### Scenario: A concurrent rebind is not overwritten

- **GIVEN** the session's owner changed between the failure and the write
- **WHEN** the retirement is attempted for the previously observed owner
- **THEN** nothing is retired and the current owner is left intact

### Requirement: Request-path retirement rebinds only an anchor the proxy injected

Retiring an owner mid-request MUST re-send the turn without the response anchor, using the body
the client supplied rather than a trimmed one. It MUST NOT be attempted when the client supplied
its own `previous_response_id`, nor when the request is pinned to an account by an uploaded
file: in both cases the request names account-scoped state the proxy may not silently discard,
and the turn MUST keep failing closed. Retirement MUST remain reversible — a later successful
claim clears it, as for any other retirement.

#### Scenario: Proxy-injected anchor

- **GIVEN** a resume whose anchor the proxy injected from the durable row
- **WHEN** its owner is retired mid-request
- **THEN** the turn is re-sent without the anchor and carries the client's full body
- **AND** the durable row ends up owned by the replacement account with no retirement marker left

#### Scenario: Client-supplied anchor

- **WHEN** the client sent its own `previous_response_id` and the owner is unavailable
- **THEN** no retirement is attempted and the turn fails closed as before

#### Scenario: File-pinned request

- **WHEN** the request is pinned to an account by an uploaded input file
- **THEN** no retirement is attempted

### Requirement: An unclaimed request-path retirement is collected

A retirement recorded on the request path MUST remain collectable by the scheduled sweep. Once
the session has had no successful activity for the grace window, the sweep MUST convert that
marker into the same form it writes itself, so the existing deletion phase can reclaim the row.
That conversion MUST NOT require the owner account to still be unavailable, because an owner
that recovered without the thread ever rebinding would otherwise leave the marker permanent.

#### Scenario: The rebind never lands

- **GIVEN** a session whose owner was retired on the request path and which was never claimed
  afterwards
- **WHEN** it has had no successful activity for the grace window and the sweep runs
- **THEN** the marker is converted to the sweep's own form
- **AND** a later sweep deletes the row once that marker is itself past the window

#### Scenario: The owner recovered meanwhile

- **GIVEN** the same session, whose retired owner has since returned to a routable status
- **WHEN** the sweep runs after the grace window
- **THEN** the marker is still converted, so the row cannot outlive every sweep
