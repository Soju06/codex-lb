## ADDED Requirements

### Requirement: Account usage panel supports confirmed usage reset

The Accounts page selected-account Usage panel SHALL expose a Reset action
inside the Usage resets row when reset-credit availability is shown. The action
SHALL require operator confirmation, SHALL consume one upstream usage reset
credit for the selected account, SHALL force-fetch upstream usage after a
successful or idempotently successful consume without sending model probe
traffic, and SHALL refresh account-related dashboard queries after success. The
dashboard SHALL NOT reduce or add permanent polling intervals to make this
reset appear sooner.

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
