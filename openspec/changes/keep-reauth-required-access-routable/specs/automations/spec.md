## ADDED Requirements

### Requirement: Automation eligibility follows request routability

Automation cycle creation, pending-run visibility, and dispatch MUST treat active and `reauth_required` accounts as eligible request targets. Accounts that are deleted, rate-limited, quota-exceeded, or deactivated MUST remain ineligible, and paused-account eligibility MUST continue to follow the cycle's explicit paused-account policy.

#### Scenario: Reauthentication warning account remains dispatchable

- **GIVEN** an automation cycle includes an account whose status is `reauth_required`
- **WHEN** that account's scheduled dispatch time arrives
- **THEN** the scheduler attempts the automation request with the account's stored access token
- **AND** it does not skip the account solely because refresh-token exchange requires reauthentication

#### Scenario: Deactivated account remains ineligible

- **GIVEN** an automation cycle includes a deactivated account
- **WHEN** that account's scheduled dispatch time arrives
- **THEN** the scheduler skips the account without dispatching upstream
