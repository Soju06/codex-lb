## ADDED Requirements

### Requirement: Fresh local bridge replacements fence detached generations

When an HTTP Responses bridge creates a fresh local session for a durable key
whose active row still names the current instance, and no reusable local
session represents that durable generation, the replacement claim MUST advance
the durable owner epoch before the replacement is published. A release from
the detached local generation MUST be fenced out and MUST NOT clear the
replacement's owner, lease, active state, or continuity anchors.

Ordinary reuse of a registered local session MUST NOT advance the owner epoch,
and an active row owned by another live instance MUST continue to follow the
existing owner-forwarding or mismatch behavior.

#### Scenario: clean-close replacement survives a late local release

- **GIVEN** a local HTTP bridge session has delivered `response.completed`
- **AND** its upstream WebSocket then closes cleanly
- **AND** retirement detaches that session before its durable release completes
- **WHEN** the next request creates a replacement for the same durable key
- **THEN** the replacement claim advances the durable owner epoch
- **AND** the detached generation's late release is fenced out
- **AND** the next request does not receive `bridge_instance_mismatch`

#### Scenario: registered local reuse keeps its generation

- **GIVEN** the durable row and a reusable registered local session represent
  the same current owner generation
- **WHEN** another compatible request reuses that session
- **THEN** the durable owner epoch is not advanced solely because of reuse

#### Scenario: a live remote owner remains protected

- **GIVEN** a durable bridge row has an unexpired lease owned by another live
  instance
- **AND** this instance previously observed its own detached generation before
  the remote owner claimed the row
- **WHEN** this instance attempts the local replacement claim
- **THEN** it revalidates the owner under the durable row lock
- **AND** it does not advance or take over the remote owner's epoch without the
  existing takeover authorization
