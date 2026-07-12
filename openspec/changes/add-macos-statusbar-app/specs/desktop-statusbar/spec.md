## ADDED Requirements

### Requirement: macOS status bar app displays local account status

The project SHALL provide a macOS status bar app for local operators. The app SHALL default to `http://127.0.0.1:2455`, SHALL fetch dashboard session state from `GET /api/dashboard-auth/session`, and SHALL fetch account status and usage from `GET /api/dashboard/overview?timeframe=7d` without requiring operators to open the web dashboard.

The status bar title SHALL show the average remaining 5h and weekly quota percentages across active accounts with telemetry, followed by the active account count over total accounts in parentheses. The normal title SHALL use the compact format `5h 93% W 7% (3/3)` without an application-name prefix. If only monthly quota telemetry is available, the title SHALL show its active-account average instead. The app menu SHALL render account cards with progress bars. The menu SHALL show each account's display name, email, plan, status pill, routing policy pill, primary/secondary/monthly quota percentages, reset countdowns, available reset-credit count and nearest expiry when present, and additional quota windows when present. Quota bars SHALL use the same remaining-capacity thresholds as the dashboard: green at 70 percent or above, amber from 30 percent through less than 70 percent, and red below 30 percent.

If dashboard authentication is required, the app SHALL expose an admin password login action and a guest login action. The app SHALL store the server URL locally, SHALL allow manual refresh, SHALL refresh periodically while running, and SHALL provide an action to open the full dashboard in the browser.

The app SHALL expose a `Launch at Login` menu toggle backed by the macOS `SMAppService.mainApp` API. The item SHALL show checked when enabled, mixed when macOS approval is required, and unchecked when disabled. Selecting the mixed state SHALL open the macOS Login Items settings.

The account panel SHALL show a visible refreshing state while a refresh is running and SHALL show the local completion time after a successful refresh. If the account menu is open when a refresh completes, the app SHALL replace the visible account panel in place so the operator does not need to close and reopen it manually. A refresh that completes while the menu is closed SHALL NOT open the menu. Account routing and status badges SHALL remain content-sized after repeated in-place refreshes instead of absorbing unused row width. Routing badges SHALL match the dashboard presentation: `burn_first` SHALL use an amber flame badge, `preserve` SHALL use a blue shield badge, and `normal` SHALL remain neutral.

#### Scenario: Local operator sees active account count

- **WHEN** the local codex-lb dashboard API is reachable and returns account summaries
- **THEN** the macOS status bar title shows active-account average quota percentages and active account count over total account count
- **AND** the menu renders visual account cards with status pills and quota progress bars

#### Scenario: Authentication required

- **WHEN** the overview request returns an authentication error
- **THEN** the status bar title indicates login is required
- **AND** the menu exposes admin password and guest login actions

#### Scenario: Server unavailable

- **WHEN** the configured codex-lb server cannot be reached
- **THEN** the status bar title indicates an error state
- **AND** the menu shows the connection error and keeps refresh and server URL actions available

#### Scenario: Reset credits are available

- **WHEN** an account summary contains one or more available reset credits
- **THEN** the account card shows the available count
- **AND** the card shows the nearest expiry as a compact countdown when the expiry is present

#### Scenario: Operator refreshes account status

- **WHEN** the operator starts a manual refresh
- **THEN** the account panel shows a refreshing indicator
- **AND** after a successful response the panel shows the local completion time
- **AND** a menu that is still open when the refresh completes shows the rebuilt account view without a manual reopen

#### Scenario: Operator enables launch at login

- **WHEN** the operator selects `Launch at Login` while it is disabled
- **THEN** the app registers its main application as a macOS login item
- **AND** the menu item reflects the resulting system status

### Requirement: macOS status bar app is packaged as a DMG

The project SHALL provide a local packaging script that compiles the status bar source into a `.app` bundle, marks the app as a background menu bar app, signs it ad hoc for local execution, and writes a compressed DMG artifact under the status bar app's `dist/` directory.

#### Scenario: Build DMG locally

- **WHEN** an operator runs the status bar DMG build target on macOS with Xcode command line tools available
- **THEN** the build produces `desktop/statusbar/dist/CodexLBStatusBar.dmg`
- **AND** the DMG contains `CodexLBStatusBar.app`
