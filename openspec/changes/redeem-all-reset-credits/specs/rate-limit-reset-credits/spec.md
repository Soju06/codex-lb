## ADDED Requirements

### Requirement: Operators can review every eligible account's credit before a bulk redeem

The Accounts page SHALL offer a **Redeem all resets** action to principals with account write access, hidden when no account is eligible. An account is eligible when its summary reports an available reset credit and its status is not `paused`, `reauth_required` or `deactivated`. The action SHALL open a confirmation listing each eligible account and its credit count, and SHALL NOT redeem anything until confirmed.

#### Scenario: Eligible accounts are listed before redeeming
- **GIVEN** three accounts where two are active with banked credits and one is paused with a banked credit
- **WHEN** the operator opens **Redeem all resets**
- **THEN** the dialog lists the two active accounts with their credit counts
- **AND** no consume request has been sent

#### Scenario: The action is not offered without a redeemable credit or write access
- **WHEN** no account is eligible, or the session is read-only
- **THEN** the **Redeem all resets** action is not shown

### Requirement: Bulk redeem reuses the per-account redemption path

On confirmation the dashboard SHALL redeem each listed account's own soonest-expiring credit through the per-account consume endpoint, once per account, sequentially, with a distinct `redeem_request_id` per account. It MUST NOT pool credits across accounts or add a bulk backend endpoint, so each redemption keeps the single-account serialization, idempotency ledger, cache invalidation, usage refresh and audit event. A failure on one account MUST NOT stop the rest. The dashboard SHALL report each account's outcome, keep the dialog open while any account failed, and refresh account data afterwards.

#### Scenario: Confirming redeems each account once
- **GIVEN** the dialog lists two accounts
- **WHEN** the operator confirms
- **THEN** one consume request is sent per listed account, one after the other, each with its own `redeem_request_id`
- **AND** the dialog closes and a success notice is shown when every account redeemed

#### Scenario: One account failing does not hide the others
- **GIVEN** the dialog lists two accounts and the first consume request is rejected
- **WHEN** the operator confirms
- **THEN** the second account is still attempted
- **AND** the dialog stays open showing the failure reason for the first account and success for the second
