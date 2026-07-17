## MODIFIED Requirements

### Requirement: Account usage panel supports confirmed usage reset

The Accounts page selected-account Usage panel SHALL expose a Reset action
inside the Usage resets row when reset-credit availability is shown. The action
SHALL require operator confirmation, SHALL consume one upstream usage reset
credit for the selected account, SHALL force-fetch upstream usage after a
successful or idempotently successful consume without sending model probe
traffic, and SHALL refresh account-related dashboard queries after success. The
dashboard SHALL NOT reduce or add permanent polling intervals to make this
reset appear sooner. When the selected account summary exposes
`reset_credit_nearest_expires_at`, the Usage resets row SHALL show the earliest
reset-credit expiry using the dashboard's local datetime formatting and a
compact remaining-time label.

#### Scenario: Usage reset row shows nearest reset-credit expiry

- **GIVEN** an active selected account is visible on the Accounts page
- **AND** the selected account summary exposes `reset_credit_nearest_expires_at`
- **WHEN** the Usage panel renders the Usage resets row
- **THEN** the row shows the earliest reset-credit expiry in local time
- **AND** the row shows a compact remaining-time label for that expiry

### Requirement: Accounts page exposes a reset-credits redeem action

The Accounts page per-account action bar SHALL render a `Reset (N)` button next to the existing Export button with matching button styling whenever the account reports `available_reset_credits > 0`, where `N` is the available reset-credit count for that account. The button SHALL be hidden when `available_reset_credits` is `0`. Activating the button SHALL open a confirmation dialog that describes redeeming the soonest-expiring banked reset credit for that account and, when credit details are available, shows the soonest credit's expiry in local time using `YYYY-MM-DD HH:MM:SS`. Confirming SHALL submit a redeem request for that account and refresh account data on success. The compact remaining-time label pinned to the reset action SHALL be controlled by the dashboard setting `show_reset_credit_expiry_badge`, defaulting to enabled.

#### Scenario: Reset action expiry label can be hidden

- **GIVEN** `show_reset_credit_expiry_badge` is disabled
- **AND** an account reports `available_reset_credits > 0` and `reset_credit_nearest_expires_at`
- **WHEN** the Accounts page renders the per-account action bar
- **THEN** the `Reset (N)` button remains visible
- **AND** the compact remaining-time label is not rendered on that button

### Requirement: AccountListItem displays a reset-credits count badge

The Accounts page `AccountListItem` SHALL render a count badge pinned to the right-upper radius of the item whenever the account reports `available_reset_credits > 0` and dashboard setting `show_reset_credit_badges` is enabled. The badge SHALL display the integer count, capped visually at `"99+"` when the count exceeds 99. The badge SHALL be absent when `available_reset_credits` is `0` or `show_reset_credit_badges` is disabled.

#### Scenario: Badge visibility follows settings

- **GIVEN** `show_reset_credit_badges` is disabled
- **AND** an account reports `available_reset_credits: 3`
- **WHEN** an `AccountListItem` renders
- **THEN** the reset-credit count badge is absent

### Requirement: Settings page exposes reset-credit controls

The Settings page SHALL expose a Reset credits section. The section SHALL allow operators to update `show_reset_credit_badges`, `auto_redeem_reset_credits_before_expiry`, and `show_reset_credit_expiry_badge` through the settings API. `show_reset_credit_badges` and `show_reset_credit_expiry_badge` SHALL default to enabled. `auto_redeem_reset_credits_before_expiry` SHALL default to disabled so upgraded deployments preserve the current manual-only redemption behavior. The automatic redemption control SHALL describe that the system attempts to redeem the soonest reset credit about five minutes before it expires. Changes to any of these three settings SHALL be included in the `settings_changed` audit entry's `changed_fields` list.

#### Scenario: Reset-credit display settings save through settings API

- **WHEN** an operator toggles reset-credit badge visibility
- **THEN** the dashboard sends `showResetCreditBadges` through the settings API

#### Scenario: Reset-credit auto redeem setting saves through settings API

- **WHEN** an operator toggles automatic reset-credit redemption
- **THEN** the dashboard sends `autoRedeemResetCreditsBeforeExpiry` through the settings API

### Requirement: Core navigation can show reset-credit count badge

The top navigation Accounts item SHALL render the total available reset-credit count badge when `show_reset_credit_badges` is enabled and the total count is greater than zero. The badge SHALL be hidden when `show_reset_credit_badges` is disabled.

#### Scenario: Top navigation reset-credit badge visibility follows settings

- **GIVEN** the settings API returns `show_reset_credit_badges: false`
- **AND** account summaries report available reset credits
- **WHEN** the top navigation renders
- **THEN** the Accounts navigation reset-credit badge is absent
