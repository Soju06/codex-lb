## ADDED Requirements

### Requirement: Accounts list reset-credit expiry warning

The Accounts list SHALL mark an available-reset-credit badge when its nearest known expiry is in the future and at most 72 hours away. The warning SHALL have a localized accessible description, follow both reset-credit count and expiry badge visibility, and disappear when credits are zero, expiry is absent or invalid, or the expiry has passed. While the list remains mounted, the warning SHALL update with the passage of time at least once per minute and when account data changes.

#### Scenario: Credit enters the warning window
- **GIVEN** an account has positive available credits and an expiry more than 72 hours away
- **WHEN** its expiry becomes at most 72 hours away
- **THEN** the badge gains the warning within one minute without a page reload

#### Scenario: Expired or hidden credit
- **WHEN** the expiry passes, the count becomes zero, or reset-credit count or expiry badges are disabled
- **THEN** the warning is absent
