## ADDED Requirements

### Requirement: Dashboard accounts section shows account availability summary

The dashboard `Accounts` section SHALL render a compact summary derived from the existing dashboard overview accounts collection. The summary SHALL show the total registered account count, the active account count, and the unavailable account count.

An account SHALL count as active only when its dashboard status normalizes to `active`. Accounts whose normalized status is `paused`, `limited`, `exceeded`, `reauth`, or `deactivated` SHALL count as unavailable.

The summary SHALL render in the `Accounts` section header row and SHALL use the project's existing foreground, muted, positive, and negative theme color conventions for light and dark mode.

#### Scenario: Mixed account states show registered, active, and unavailable counts

- **WHEN** `GET /api/dashboard/overview` returns three accounts with statuses `active`, `paused`, and `rate_limited`
- **THEN** the dashboard `Accounts` section header shows `3 registered`
- **AND** shows `1 active`
- **AND** shows `2 unavailable`

#### Scenario: Only normalized active accounts count as active

- **WHEN** `GET /api/dashboard/overview` returns accounts with statuses `active`, `quota_exceeded`, `reauth_required`, and `deactivated`
- **THEN** only the `active` account contributes to the active count
- **AND** the other three accounts contribute to the unavailable count

#### Scenario: Theme-aware colors match dashboard conventions

- **WHEN** the dashboard renders in light mode or dark mode
- **THEN** the registered count uses foreground styling
- **AND** the labels use muted-foreground styling
- **AND** the active count uses the dashboard positive green styling
- **AND** the unavailable count uses the dashboard negative red styling
