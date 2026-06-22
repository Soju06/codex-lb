## ADDED Requirements

### Requirement: Accounts page exposes a reset-credits redeem action

The Accounts page per-account action bar SHALL render a `Reset (N)` button next to the existing Export button with matching button styling whenever the account reports `available_reset_credits > 0`, where `N` is the available reset-credit count for that account. The button SHALL be hidden when `available_reset_credits` is `0`. Activating the button SHALL open a confirmation dialog that describes redeeming the soonest-expiring banked reset credit for that account and, when credit details are available, shows the soonest credit's expiry in local time using `YYYY-MM-DD HH:MM:SS`. Confirming SHALL submit a redeem request for that account and refresh account data on success.

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

### Requirement: AccountListItem displays a reset-credits count badge

The Accounts page `AccountListItem` SHALL render a count badge pinned to the right-upper radius of the item whenever the account reports `available_reset_credits > 0`. The badge SHALL display the integer count, capped visually at `"99+"` when the count exceeds 99. The badge SHALL be absent when `available_reset_credits` is `0`.

#### Scenario: Badge shows the available count
- **WHEN** an `AccountListItem` renders for an account with `available_reset_credits: 3`
- **THEN** a count badge pinned to the item's right-upper radius displays `3`

#### Scenario: Badge caps at 99+
- **WHEN** an `AccountListItem` renders for an account with `available_reset_credits: 120`
- **THEN** the count badge displays `99+`

#### Scenario: Badge absent when zero
- **WHEN** an `AccountListItem` renders for an account with `available_reset_credits: 0`
- **THEN** no count badge is rendered

### Requirement: Accounts page can sort by available reset credits

The Accounts page sort selector SHALL offer a "Most reset credits" option and SHALL use it as the default Accounts page ordering. That ordering SHALL sort accounts by `available_reset_credits` descending. Ties SHALL be broken by `reset_credit_nearest_expires_at` ascending (soonest expiring first), and accounts with no expiry SHALL sort after accounts that have one.

#### Scenario: More available credits sorts first
- **WHEN** the operator opens the Accounts page with the default sort mode
- **AND** account A has `available_reset_credits: 4` and account B has `available_reset_credits: 1`
- **THEN** account A appears before account B

#### Scenario: Tie breaks by soonest expiry
- **WHEN** two accounts have equal `available_reset_credits`
- **AND** one account's soonest credit expires before the other's
- **THEN** the account with the earlier `reset_credit_nearest_expires_at` appears first

### Requirement: Dashboard accounts section exposes a reset-credits redeem action

The Dashboard Accounts section SHALL render a reset action next to the existing Details action in both the table and grid views for any account with `available_reset_credits > 0`. The grid view label SHALL read `Reset (N)`. The table view MAY remain icon-only, but its tooltip/title SHALL include the available reset-credit count. The action SHALL be absent when `available_reset_credits` is `0`. Activating the action SHALL open the same confirmation flow as the Accounts page reset action.

#### Scenario: Table view shows reset next to details
- **WHEN** the Dashboard Accounts section renders in table view for an account with `available_reset_credits > 0`
- **THEN** a "Reset" action appears in the same action cell as the Details action

#### Scenario: Grid view shows reset next to details
- **WHEN** the Dashboard Accounts section renders in grid view for an account with `available_reset_credits > 0`
- **THEN** a `Reset (N)` button appears next to the Details button on the account card

#### Scenario: Reset action absent when no credits
- **WHEN** an account reports `available_reset_credits: 0`
- **THEN** the Dashboard Accounts section renders no "Reset" action for that account in either view

### Requirement: Dashboard header shows the total available reset-credit count

The dashboard top navigation SHALL render the total available reset-credit count on the Accounts tab, pinned to the tab's upper-right radius. The total SHALL equal the sum of `available_reset_credits` across the current account list data. The badge SHALL display `99+` when the total exceeds 99 and SHALL be hidden when the total is 0.

#### Scenario: Accounts tab shows the summed total
- **WHEN** the current account list totals `available_reset_credits` to `14`
- **THEN** the Accounts nav tab displays a badge with `14`

#### Scenario: Accounts tab caps large totals
- **WHEN** the current account list totals `available_reset_credits` to `120`
- **THEN** the Accounts nav tab displays a badge with `99+`

#### Scenario: Accounts tab hides empty totals
- **WHEN** every account reports `available_reset_credits: 0`
- **THEN** the Accounts nav tab displays no reset-credit badge

### Requirement: Reset actions display a single-unit expiry countdown

Every "Reset" button SHALL display a small countdown label of the soonest-expiring credit's expiry, formatted as a single time unit: `"${d}d"` for any remaining duration of one day or more, `"${h}h"` for durations under one day but at least one hour, `"${m}m"` for durations under one hour but at least one minute, and `"now"` for durations under one minute. The label SHALL render in the destructive/red color when the remaining duration is strictly less than 7 days, and in the default muted color otherwise.

#### Scenario: Days format for duration at or above one day
- **WHEN** a Reset button renders for a credit whose `expires_at` is 12 days away
- **THEN** the countdown label reads `12d`
- **AND** the label uses the default muted color

#### Scenario: Red color under seven days
- **WHEN** a Reset button renders for a credit whose `expires_at` is 6 days away
- **THEN** the countdown label reads `6d`
- **AND** the label uses the destructive/red color

#### Scenario: Hours and minutes use the smaller unit
- **WHEN** a Reset button renders for a credit whose `expires_at` is 13 hours away
- **THEN** the countdown label reads `13h`
- **AND** the label uses the destructive/red color

#### Scenario: Sub-minute duration shows now
- **WHEN** a Reset button renders for a credit whose `expires_at` is 30 seconds away
- **THEN** the countdown label reads `now`
- **AND** the label uses the destructive/red color
