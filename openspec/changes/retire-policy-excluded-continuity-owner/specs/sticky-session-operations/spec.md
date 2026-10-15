## ADDED Requirements

### Requirement: A policy-excluded continuity owner is retired on the same terms as an unavailable one

When a bridge turn fails because its continuity owner is outside the eligible account policy,
the proxy MUST apply the same request-path retirement decision it applies to an unavailable
owner. The owner MUST be retired only when its own status is one of the unavailable statuses
and it cannot return before the turn's request budget expires. An owner that is healthy and
merely excluded by policy MUST NOT be retired, and the turn MUST keep failing closed with the
policy-conflict error.

#### Scenario: Rate-limited owner excluded by its routing policy

- **GIVEN** a bridged thread whose owner is rate limited with a reset horizon after the
  request's deadline
- **AND** account selection reports that owner as outside the eligible account policy
- **WHEN** the turn fails with that policy conflict
- **THEN** the owner is retired and the turn is served on a healthy account within the same
  request

#### Scenario: Healthy owner excluded by policy

- **GIVEN** a bridged thread whose owner is active
- **WHEN** the turn fails because that owner is outside the eligible account policy
- **THEN** no retirement is recorded
- **AND** the turn fails closed with the policy-conflict error
