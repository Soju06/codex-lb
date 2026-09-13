## ADDED Requirements

### Requirement: Dashboard route failures preserve a recoverable shell

The authenticated dashboard SHALL retain its header, main landmark, status
region, and React root when a route is unknown, pending, or fails to render.
Unknown routes MUST render localized Not Found status and a keyboard Dashboard
link. Pending lazy routes MUST render visible loading status. Rejected lazy
imports MUST render an announced error with keyboard reload and Dashboard
actions. Reload MUST fully navigate the current URL so the browser requests the
current asset graph. Route-level code splitting MUST remain intact.

#### Scenario: Unknown route retains shell

- **WHEN** an authenticated operator opens an unknown path
- **THEN** shell landmarks remain rendered
- **AND** Not Found receives focus
- **AND** a keyboard Dashboard link is available

#### Scenario: Pending lazy route is visible

- **WHEN** a matched lazy import remains pending
- **THEN** shell landmarks remain
- **AND** main renders a visible loading status

#### Scenario: Rejected lazy route is contained

- **WHEN** a lazy page import rejects
- **THEN** the React root and shell remain rendered
- **AND** an announced error receives focus
- **AND** keyboard reload and Dashboard actions are available

#### Scenario: Reload retries through current assets

- **WHEN** the operator activates reload after a lazy import rejection
- **THEN** the browser fully navigates the current URL
- **AND** the route renders when its chunk becomes available
