## ADDED Requirements

### Requirement: Weekly-only quota recovery

When the upstream usage API reports a weekly quota in the primary slot and no secondary or monthly slot, usage refresh MUST treat that weekly sample as the long window for quota-status recovery. After the quota cooldown, an available weekly sample MUST reactivate a quota-exceeded account. The sample MUST remain stored in its original slot.

For routing, a weekly primary row MUST supply weekly quota without creating a five-hour quota. If the persisted account is active and a fresh available weekly sample was recorded after a replica's earlier quota block, selection MUST clear that replica's stale quota reset, cooldown, block marker, and error backoff. A real Team five-hour limit MUST retain its rate-limit hold until eligible recovery evidence or its reset deadline.

#### Scenario: Professional account has weekly quota remaining

- **GIVEN** a quota-exceeded Professional account has passed the quota cooldown
- **WHEN** fresh usage reports 3 percent consumed in a weekly primary window and no secondary window
- **THEN** the account becomes active
- **AND** its weekly usage remains stored in the primary slot

#### Scenario: Reserve account remains selectable and sticky

- **GIVEN** a Professional account has 6 percent weekly usage in its only quota window
- **AND** a Team account has exhausted its five-hour quota while weekly quota remains
- **AND** the Professional account is active with fresh post-block usage
- **WHEN** routing selects a new or existing sticky request
- **THEN** the Professional account is eligible without a synthetic five-hour window
- **AND** its stale replica-local quota cooldown does not exclude it
- **AND** the sticky owner remains the Professional account

### Requirement: Operator reactivation of a quota-exceeded account

The dashboard MUST offer an enabled Resume action for a quota-exceeded account when the operator has account write access and no account action is pending. Invoking Resume MUST use the existing account reactivation endpoint. The action MUST remain disabled in read-only or busy states. A subsequent upstream quota rejection MAY mark the account quota-exceeded again under the normal account-scoped penalty rules.

#### Scenario: Operator retries a reserve account after stale quota exhaustion

- **GIVEN** an account is displayed as quota-exceeded
- **AND** the operator has account write access
- **WHEN** the operator chooses Resume
- **THEN** the dashboard submits reactivation for that account
- **AND** the account can be considered for routing after the endpoint succeeds
