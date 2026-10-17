## ADDED Requirements

### Requirement: Step-up dialog with transparent retry

The API client SHALL own the step-up flow: when a request answers `403 step_up_required` it SHALL open the one shared step-up dialog with the fields named by `details.methods` (a password field for `password`, a six-digit authenticator field for `totp`, both when both are listed), call `POST /api/dashboard-auth/step-up` with what was entered, refresh the session on success, and replay the original request exactly once, returning the replayed response to the caller; cancelling SHALL reject the original call with the `step_up_required` error and SHALL NOT retry. A `step_up_required` answered by the replay SHALL be thrown, not looped. Concurrent gated requests SHALL wait on the same dialog and share its answer. When a request answers `403 step_up_unavailable` the client SHALL show a toast titled "Confirmation not possible" with the enrol-two-factor-or-set-a-password explanation and an action that opens `/settings#access`, and SHALL throw the error. Any other `403` SHALL be left untouched. The dialog SHALL be mounted once in the application shell; the store SHALL keep the session's `stepUp` block.

#### Scenario: Saving a security setting asks once and completes

- **GIVEN** a password account whose step-up is stale
- **WHEN** it saves a security setting and the API answers `403 step_up_required` with `methods: ["password"]`
- **THEN** the dialog shows only a password field, and after a correct password the save completes without the caller seeing an error

#### Scenario: Account without a factor

- **WHEN** a request answers `403 step_up_unavailable`
- **THEN** the toast with the enrol-two-factor message and the My sign-in action is shown and the request fails

### Requirement: My sign-in TOTP control for every account

The Access card's My sign-in tab SHALL render the TOTP control for any signed-in account (`user` present) as well as for a password session, so a reverse-proxy account without a password can enrol the authenticator it needs for step-up. The control's configured/not-configured state SHALL follow the session's `totpConfigured` (the account's own secret), not the settings row.

#### Scenario: Reverse-proxy account enrols

- **GIVEN** a trusted-header account with `passwordSessionActive` false and `totpConfigured` false
- **WHEN** the My sign-in tab renders
- **THEN** the TOTP control offers Enable TOTP
