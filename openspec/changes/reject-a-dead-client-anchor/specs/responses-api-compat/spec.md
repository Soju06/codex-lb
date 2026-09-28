## ADDED Requirements

### Requirement: A client anchor on an unreachable owner is rejected, not deferred

When an HTTP session bridge request fails because its continuity owner account is unavailable,
the request carries a client-supplied `previous_response_id`, and that owner cannot return
before the request's own budget expires, the proxy MUST retire the owner and answer with an
explicit previous-response rejection rather than a retryable failure. The rejection MUST reach
the client unmasked, MUST name a remedy the client can act on — resending the conversation or
starting a new one — and MUST NOT be marked with the pre-submit provenance that would route the
turn to the plain HTTP upstream. An owner whose reset horizon falls inside the budget MUST
still be waited for, and a request whose anchor the proxy injected itself MUST continue to use
the rebinding path instead.

#### Scenario: Client anchor on a durably unavailable owner

- **GIVEN** a bridged thread whose owner account is paused
- **WHEN** the client resumes it with its own `previous_response_id`
- **THEN** the response is an explicit previous-response rejection, not a retryable
  owner-unavailable failure
- **AND** the message tells the client to resend the conversation or start a new one
- **AND** the owner is retired

#### Scenario: The client's recovery binds elsewhere

- **GIVEN** that rejection has retired the owner
- **WHEN** the client resends the same thread without an anchor
- **THEN** the request is served on a healthy account

#### Scenario: Owner returning inside the budget

- **WHEN** the unavailable owner carries a reset horizon before the request's deadline
- **THEN** no rejection is issued and the existing behaviour is unchanged

#### Scenario: Proxy-injected anchor

- **WHEN** the anchor was injected by the proxy rather than sent by the client
- **THEN** the rebinding path handles it and no rejection is issued

### Requirement: A retirement race answers terminally for every racer

Retiring a continuity owner MUST report whether that owner is retired once the call returns, not
whether this particular caller performed the write. When duplicate requests race, the one whose
compare-and-set matches nothing because another already retired the same owner MUST receive the
same terminal answer, not the retryable owner-unavailable failure it replaced. An owner that was
deliberately kept — recovered, or returning inside the deadline — MUST still report as not
retired, and a session that no longer exists MUST NOT report as retired.

#### Scenario: The loser of the race

- **GIVEN** two requests holding the same pre-retirement lookup
- **WHEN** the first retires the owner and the second's compare-and-set matches nothing
- **THEN** both are told the owner is retired
- **AND** the marker is written once

#### Scenario: Kept, not retired

- **WHEN** the owner recovered, or its reset horizon falls inside the deadline
- **THEN** the call reports the owner as not retired
