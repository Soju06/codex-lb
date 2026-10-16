## ADDED Requirements

### Requirement: Confirmed reset-credit refresh may release an expired quota block

A forced usage refresh triggered by an upstream reset-credit redemption MUST receive the redemption completion time only when upstream reports `code == "reset"` and `windows_reset > 0`. When that confirmation time is strictly later than a rate-limited account's `blocked_at`, the refresh MUST bypass the persisted rate-limit cooldown deadline check and continue through the existing quota availability checks. If confirmation is absent, does not postdate the block, or the refreshed quota remains exhausted, the account MUST remain rate-limited. This exception MUST NOT change `QUOTA_EXCEEDED` recovery or ordinary usage refresh behavior.

#### Scenario: Confirmed reset credit releases a rate-limited account with available weekly quota

- **GIVEN** a `RATE_LIMITED` account has a future persisted weekly reset deadline and a `blocked_at` marker
- **WHEN** upstream reset-credit redemption returns `code="reset"` and `windows_reset > 0`
- **AND** the forced usage refresh completes after `blocked_at` and reports available quota
- **THEN** the account becomes `ACTIVE`
- **AND** its `reset_at` and `blocked_at` markers are cleared

#### Scenario: Unconfirmed reset credit does not bypass the cooldown

- **GIVEN** a `RATE_LIMITED` account has a future persisted reset deadline
- **WHEN** redemption does not return both `code="reset"` and `windows_reset > 0`
- **THEN** the account remains `RATE_LIMITED` while the deadline is in the future

#### Scenario: Confirmed redemption with exhausted refreshed quota remains blocked

- **GIVEN** a `RATE_LIMITED` account has a future persisted reset deadline
- **WHEN** upstream confirms a reset after `blocked_at`
- **AND** the forced usage refresh still reports the primary quota at 100 percent
- **THEN** the account remains `RATE_LIMITED`
