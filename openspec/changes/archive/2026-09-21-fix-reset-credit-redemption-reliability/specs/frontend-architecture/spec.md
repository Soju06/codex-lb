## MODIFIED Requirements

### Requirement: Accounts page exposes a reset-credits redeem action

The Accounts page per-account action bar SHALL render a `Reset (N)` button next to the existing Export button with matching button styling whenever the account reports `available_reset_credits > 0`, where `N` is the available reset-credit count for that account. The button SHALL be hidden when `available_reset_credits` is `0` and no post-redeem reconciliation is pending. During pending reconciliation, a disabled pending indicator SHALL distinguish a temporarily missing snapshot from a fresh zero count. Activating the button SHALL open a confirmation dialog that describes redeeming the soonest-expiring banked reset credit for that account and, when credit details are available, shows the soonest credit's expiry in local time using `YYYY-MM-DD HH:MM:SS`. Confirming SHALL submit a redeem request for that account and reconcile only the target account summary, trends, and reset-credit details. The mutation SHALL NOT invalidate or refetch the complete account list or unrelated accounts' queries. Confirmed reset, explicit no-reset, and pending/unknown outcomes SHALL be displayed distinctly; a successful HTTP response alone SHALL NOT produce a reset-success message. The compact remaining-time label pinned to the reset action SHALL be controlled by the dashboard setting `show_reset_credit_expiry_badge`, defaulting to enabled.

#### Scenario: Reset button mirrors Export styling and placement
- **WHEN** the Accounts page renders the per-account action bar for an account with `available_reset_credits > 0`
- **THEN** a `Reset (N)` button appears immediately next to the Export button
- **AND** the button uses the same size, variant, and class as the Export button

#### Scenario: Reset button hidden when no credits available
- **WHEN** an account reports `available_reset_credits: 0` and no post-redeem reconciliation is pending
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

#### Scenario: One reset updates one account
- **GIVEN** account X is selected and account Y also has a Reset button
- **WHEN** X is successfully redeemed
- **THEN** targeted authoritative data is merged into X's existing cached entry
- **AND** Y's data and button remain visible without a full-list refetch
- **AND** selection, filters, pagination, and scroll are preserved

#### Scenario: Temporarily absent snapshot is pending
- **WHEN** targeted reconciliation returns a null reset-credit freshness timestamp
- **THEN** the dashboard shows pending refresh instead of treating the missing snapshot as authoritative zero credits
- **AND** it performs bounded targeted reconciliation without another consume

#### Scenario: Unconfirmed result has no success toast
- **WHEN** consume returns an explicit no-reset or unknown result
- **THEN** the dashboard displays that outcome without claiming quota was restored

### Requirement: Dashboard accounts section exposes a reset-credits redeem action

The Dashboard Accounts section SHALL render a reset action next to the existing Details action in both the table and grid views for any account with `available_reset_credits > 0`. The grid view label SHALL read `Reset (N)`. The table view MAY remain icon-only, but its tooltip/title SHALL include the available reset-credit count. The action SHALL be absent when `available_reset_credits` is `0` and no post-redeem reconciliation is pending; pending reconciliation SHALL use the same disabled pending indicator as the Accounts page. Activating the action SHALL open the same confirmation flow as the Accounts page reset action.

The Dashboard table and grid SHALL share targeted post-redeem reconciliation with the Accounts page. Aggregate totals SHALL reflect the changed account without replacing the complete account collection. Any aggregate-only refresh SHALL be coalesced and SHALL NOT invalidate the account list.

#### Scenario: Table view shows reset next to details
- **WHEN** the Dashboard Accounts section renders in table view for an account with `available_reset_credits > 0`
- **THEN** a "Reset" action appears in the same action cell as the Details action

#### Scenario: Grid view shows reset next to details
- **WHEN** the Dashboard Accounts section renders in grid view for an account with `available_reset_credits > 0`
- **THEN** a `Reset (N)` button appears next to the Details button on the account card

#### Scenario: Reset action absent when no credits
- **WHEN** an account reports `available_reset_credits: 0` and no post-redeem reconciliation is pending
- **THEN** the Dashboard Accounts section renders no "Reset" action for that account in either view

#### Scenario: Dashboard reset preserves unrelated rows
- **WHEN** an operator redeems account X from either the dashboard table or grid
- **THEN** only X's summary and account-specific queries are reconciled
- **AND** unrelated cards, counts, and Reset buttons remain populated

### Requirement: Settings page exposes reset-credit controls

The Settings page SHALL expose a Reset credits section. The section SHALL allow operators to update `show_reset_credit_badges`, `auto_redeem_reset_credits_before_expiry`, and `show_reset_credit_expiry_badge` through the settings API. `show_reset_credit_badges` and `show_reset_credit_expiry_badge` SHALL default to enabled. `auto_redeem_reset_credits_before_expiry` SHALL default to disabled so upgraded deployments preserve the current manual-only redemption behavior. The automatic redemption control SHALL describe that the system attempts to redeem the soonest reset credit when it has at most one hour remaining, subject to account eligibility and upstream availability. Changes to any of these three settings SHALL be included in the `settings_changed` audit entry's `changed_fields` list.

#### Scenario: Reset-credit display settings save through settings API

- **WHEN** an operator toggles reset-credit badge visibility
- **THEN** the dashboard sends `showResetCreditBadges` through the settings API

#### Scenario: Reset-credit auto redeem setting saves through settings API

- **WHEN** an operator toggles automatic reset-credit redemption
- **THEN** the dashboard sends `autoRedeemResetCreditsBeforeExpiry` through the settings API

#### Scenario: Auto-redeem description matches scheduling boundary
- **WHEN** the Settings page renders the automatic redemption control
- **THEN** its description states the one-hour window
- **AND** the control remains disabled by default for new settings rows


### Requirement: Account usage panel supports confirmed usage reset

The Accounts page selected-account Usage panel SHALL expose a Reset action
inside the Usage resets row when reset-credit availability is shown. The action
SHALL require operator confirmation, SHALL consume one upstream usage reset
credit for the selected account, SHALL force-fetch upstream usage after a
successful or idempotently successful consume without sending model probe
traffic, and SHALL reconcile the selected account summary and invalidate only that account's usage, trend, and reset-credit queries after success. It SHALL NOT trigger a full account-list or dashboard-overview refetch. The
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
- **AND** the selected account summary is merged into existing list/dashboard caches and only that account's usage, trend, and reset-credit queries are invalidated
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
