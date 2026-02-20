## MODIFIED Requirements

### Requirement: SPA routing
The application SHALL use React Router v6 for client-side routing with four routes: `/dashboard`, `/accounts`, `/settings`, `/firewall`. The root path `/` SHALL redirect to `/dashboard`. FastAPI SHALL serve `index.html` for all unmatched routes as a SPA fallback.

#### Scenario: Direct navigation to route
- **WHEN** a user navigates directly to `/firewall` in the browser
- **THEN** FastAPI serves `index.html` and React Router renders the Firewall page

#### Scenario: Client-side navigation
- **WHEN** a user clicks the "Firewall" tab from another page
- **THEN** the URL changes to `/firewall` without full page reload and the Firewall page renders

## ADDED Requirements

### Requirement: Firewall page in React dashboard
The React dashboard MUST provide a Firewall page that displays current mode (`allow_all` or `allowlist_active`) and allows adding/removing IP addresses via `/api/firewall/ips`.

#### Scenario: Firewall page loads list
- **WHEN** user opens `/firewall`
- **THEN** frontend requests `GET /api/firewall/ips` and renders mode + entries

#### Scenario: User adds IP entry
- **WHEN** user submits a valid IP in firewall form
- **THEN** frontend calls `POST /api/firewall/ips` and refreshes rendered list

#### Scenario: User removes IP entry
- **WHEN** user confirms deletion for an existing firewall entry
- **THEN** frontend calls `DELETE /api/firewall/ips/{ip}` and refreshes rendered list
