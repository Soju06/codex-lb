## ADDED Requirements

### Requirement: Settings page exposes reset-credit controls

The Settings page SHALL expose a Reset credits section. The section SHALL allow operators to update `show_reset_credit_badges`, `auto_redeem_reset_credits_before_expiry`, and `show_reset_credit_expiry_badge` through the settings API. `show_reset_credit_badges` and `show_reset_credit_expiry_badge` SHALL default to enabled. `auto_redeem_reset_credits_before_expiry` SHALL default to disabled so upgraded deployments preserve the current manual-only redemption behavior. The automatic redemption control SHALL describe that the system attempts to redeem the soonest reset credit about five minutes before it expires. Changes to any of these three settings SHALL be included in the `settings_changed` audit entry's `changed_fields` list.

#### Scenario: Reset-credit display settings save through settings API

- **WHEN** an operator toggles reset-credit badge visibility
- **THEN** the dashboard sends `showResetCreditBadges` through the settings API

#### Scenario: Reset-credit auto redeem setting saves through settings API

- **WHEN** an operator toggles automatic reset-credit redemption
- **THEN** the dashboard sends `autoRedeemResetCreditsBeforeExpiry` through the settings API

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

#### Scenario: Confirmed account usage reset consumes one credit
- **GIVEN** an active selected account is visible on the Accounts page
- **AND** the selected account has at least one available usage reset credit
- **WHEN** the operator clicks the Usage panel Reset action
- **AND** confirms the dialog
- **THEN** the dashboard sends a usage reset consume request for the selected account
- **AND** codex-lb does not send a model probe request
- **AND** account-related usage, trend, reset-credit, and dashboard summary
  queries are invalidated after success
- **AND** no reset-credit availability query is configured with a permanent
  refetch interval

#### Scenario: Dismissed account usage reset does not consume a credit
- **GIVEN** an active selected account is visible on the Accounts page
- **WHEN** the operator clicks the Usage panel Reset action
- **AND** cancels the dialog
- **THEN** the dashboard does not send a usage reset consume request

#### Scenario: Usage reset row shows nearest reset-credit expiry
- **GIVEN** an active selected account is visible on the Accounts page
- **AND** the selected account summary exposes `reset_credit_nearest_expires_at`
- **WHEN** the Usage panel renders the Usage resets row
- **THEN** the row shows the earliest reset-credit expiry in local time
- **AND** the row shows a compact remaining-time label for that expiry

### Requirement: Accounts page exposes a reset-credits redeem action

The Accounts page per-account action bar SHALL render a `Reset (N)` button next to the existing Export button with matching button styling whenever the account reports `available_reset_credits > 0`, where `N` is the available reset-credit count for that account. The button SHALL be hidden when `available_reset_credits` is `0`. Activating the button SHALL open a confirmation dialog that describes redeeming the soonest-expiring banked reset credit for that account and, when credit details are available, shows the soonest credit's expiry in local time using `YYYY-MM-DD HH:MM:SS`. Confirming SHALL submit a redeem request for that account and refresh account data on success. The compact remaining-time label pinned to the reset action SHALL be controlled by the dashboard setting `show_reset_credit_expiry_badge`, defaulting to enabled.

#### Scenario: Reset button mirrors Export styling and placement
- **WHEN** the Accounts page renders the per-account action bar for an account with `available_reset_credits > 0`
- **THEN** a `Reset (N)` button appears immediately next to the Export button
- **AND** the button uses the same size, variant, and class as the Export button

#### Scenario: Reset button hidden when no credits available
- **WHEN** an account reports `available_reset_credits: 0`
- **THEN** the per-account action bar renders no "Reset" button

#### Scenario: Confirmation required before redeem
- **WHEN** the operator clicks the "Reset" button
- **THEN** a confirmation dialog opens describing the soonest-expiring banked reset-credit redeem action
- **AND** no redeem request is sent until the operator confirms

#### Scenario: Confirmation dialog shows local expiry timestamp
- **WHEN** the operator opens the reset-credit confirmation dialog and credit details include an expiry timestamp
- **THEN** the dialog renders the credit expiry in local time using `YYYY-MM-DD HH:MM:SS`

#### Scenario: Reset action expiry label can be hidden
- **GIVEN** `show_reset_credit_expiry_badge` is disabled
- **AND** an account reports `available_reset_credits > 0` and `reset_credit_nearest_expires_at`
- **WHEN** the Accounts page renders the per-account action bar
- **THEN** the `Reset (N)` button remains visible
- **AND** the compact remaining-time label is not rendered on that button

### Requirement: AccountListItem displays a reset-credits count badge

The Accounts page `AccountListItem` SHALL render a count badge pinned to the right-upper radius of the item whenever the account reports `available_reset_credits > 0` and dashboard setting `show_reset_credit_badges` is enabled. The badge SHALL display the integer count, capped visually at `"99+"` when the count exceeds 99. The badge SHALL be absent when `available_reset_credits` is `0` or `show_reset_credit_badges` is disabled.

#### Scenario: Badge shows the available count
- **WHEN** an `AccountListItem` renders for an account with `available_reset_credits: 3`
- **THEN** a count badge pinned to the item's right-upper radius displays `3`

#### Scenario: Badge caps at 99+
- **WHEN** an `AccountListItem` renders for an account with `available_reset_credits: 120`
- **THEN** the count badge displays `99+`

#### Scenario: Badge absent when zero
- **WHEN** an `AccountListItem` renders for an account with `available_reset_credits: 0`
- **THEN** no count badge is rendered

#### Scenario: Badge visibility follows settings
- **GIVEN** `show_reset_credit_badges` is disabled
- **AND** an account reports `available_reset_credits: 3`
- **WHEN** an `AccountListItem` renders
- **THEN** the reset-credit count badge is absent

### Requirement: Dashboard header shows the total available reset-credit count

The dashboard top navigation SHALL render the total available reset-credit count on the Accounts tab, pinned to the tab's upper-right radius. The total SHALL equal the sum of `available_reset_credits` across the current account list data. The badge SHALL display `99+` when the total exceeds 99 and SHALL be hidden when the total is 0. The badge SHALL also be hidden when dashboard setting `show_reset_credit_badges` is disabled.

#### Scenario: Accounts tab shows the summed total
- **WHEN** the current account list totals `available_reset_credits` to `14`
- **THEN** the Accounts nav tab displays a badge with `14`

#### Scenario: Accounts tab caps large totals
- **WHEN** the current account list totals `available_reset_credits` to `120`
- **THEN** the Accounts nav tab displays a badge with `99+`

#### Scenario: Accounts tab hides empty totals
- **WHEN** every account reports `available_reset_credits: 0`
- **THEN** the Accounts nav tab displays no reset-credit badge

#### Scenario: Top navigation reset-credit badge visibility follows settings

- **GIVEN** the settings API returns `show_reset_credit_badges: false`
- **AND** account summaries report available reset credits
- **WHEN** the top navigation renders
- **THEN** the Accounts navigation reset-credit badge is absent
