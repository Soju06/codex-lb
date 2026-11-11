## ADDED Requirements

### Requirement: Confirmed reset-credit refresh may release the quota block it reset

A forced usage refresh triggered by an upstream reset-credit redemption MUST receive the time the redemption request was started, read before the consume request is sent, and only when upstream reports `code == "reset"` and `windows_reset > 0`. The refresh MUST bypass a `RATE_LIMITED` account's persisted cooldown deadline only when all of the following hold: the whole second of `blocked_at` precedes that start time; the account's persisted `reset_at` matches, within five seconds, the baseline reset deadline of exactly one quota window, where the baseline is captured before the consume request is sent and includes only rows from the newest usage fetch; and the fresh usage snapshot reports that window with a reset deadline more than five seconds away from its baseline deadline. After the bypass, the existing quota availability checks MUST still apply. In every other case the account MUST remain rate-limited until its cooldown ends. This exception MUST NOT change `QUOTA_EXCEEDED` recovery or ordinary usage refresh behavior.

#### Scenario: Confirmed reset credit releases a rate-limited account with available weekly quota

- **GIVEN** a `RATE_LIMITED` account whose persisted `reset_at` equals its weekly window's reset deadline, with a `blocked_at` marker
- **WHEN** upstream reset-credit redemption returns `code="reset"` and `windows_reset > 0`
- **AND** the forced usage refresh reports that window with a new reset deadline and available quota
- **THEN** the account becomes `ACTIVE`
- **AND** its `reset_at` and `blocked_at` markers are cleared

#### Scenario: Automatic pre-expiry redemption releases a rate-limited account

- **GIVEN** a `RATE_LIMITED` account whose persisted `reset_at` equals its weekly window's reset deadline
- **AND** automatic reset-credit redemption before expiry is enabled and a credit is inside the redemption window
- **WHEN** the scheduler redeems the credit and upstream returns `code="reset"` and `windows_reset > 0`
- **AND** the forced usage refresh reports that window with a new reset deadline and available quota
- **THEN** the account becomes `ACTIVE`

#### Scenario: Unconfirmed reset credit does not bypass the cooldown

- **GIVEN** a `RATE_LIMITED` account has a future persisted reset deadline
- **WHEN** redemption does not return both `code="reset"` and `windows_reset > 0`
- **THEN** the account remains `RATE_LIMITED` while the deadline is in the future

#### Scenario: A refresh recorded during the redemption does not hide the blocked window

- **GIVEN** a `RATE_LIMITED` account whose persisted `reset_at` equals its weekly window's reset deadline
- **WHEN** another usage refresh records the post-reset weekly deadline while the redemption is in flight
- **AND** the redemption returns `code="reset"` and `windows_reset > 0` and the forced refresh reports available quota
- **THEN** the blocked window is still identified from the baseline captured before the consume request, whatever that refresh's `recorded_at`
- **AND** the account becomes `ACTIVE`

#### Scenario: A row from an older fetch does not make the block ambiguous

- **GIVEN** a `RATE_LIMITED` account whose persisted `reset_at` equals its weekly primary window's reset deadline
- **AND** an older usage fetch left a secondary row carrying the same deadline
- **WHEN** upstream confirms a reset credit and the forced usage refresh reports the weekly window with a new deadline and available quota
- **THEN** only the newest fetch counts toward the baseline, and the account becomes `ACTIVE`

#### Scenario: Block not bound to exactly one quota window stays blocked

- **GIVEN** a `RATE_LIMITED` account whose persisted `reset_at` matches no quota window's reset deadline (for example a Retry-After or model-scoped throttle), or matches more than one
- **WHEN** upstream confirms a reset credit and the refreshed usage shows available quota
- **THEN** the account remains `RATE_LIMITED`

#### Scenario: Blocked window that did not reset stays blocked

- **GIVEN** a `RATE_LIMITED` account whose persisted `reset_at` matches its weekly window's reset deadline
- **WHEN** upstream confirms a reset credit but the refreshed weekly window still reports the persisted deadline
- **THEN** the account remains `RATE_LIMITED`

#### Scenario: A 429 recorded during the redemption is not released

- **GIVEN** a reset-credit redemption request has started
- **WHEN** a new 429 sets `blocked_at` to a time in or after the second in which the redemption started
- **THEN** the forced refresh for that redemption does not release the account

#### Scenario: Confirmed redemption with exhausted refreshed quota remains blocked

- **GIVEN** a `RATE_LIMITED` account has a future persisted reset deadline
- **WHEN** upstream confirms a reset whose window is bound to the block
- **AND** the forced usage refresh still reports the primary quota at 100 percent
- **THEN** the account remains `RATE_LIMITED`
