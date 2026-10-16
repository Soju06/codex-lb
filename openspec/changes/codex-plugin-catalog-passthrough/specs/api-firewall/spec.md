## MODIFIED Requirements

### Requirement: Firewall enforcement for protected proxy paths
The application MUST enforce firewall allowlist for proxy-facing paths `/backend-api/codex/*`, `/v1/*`, and the Codex plugin-catalog passthrough (`/ps/plugins/*`, `/plugins/featured`), which spends pool credentials upstream.

#### Scenario: Allowlist disabled when empty
- **WHEN** allowlist is empty
- **THEN** protected proxy requests are allowed

#### Scenario: Allowlist active blocks unlisted client
- **WHEN** allowlist contains one or more IP entries and request client IP is not listed
- **THEN** protected proxy request returns HTTP 403 with OpenAI-style error code `ip_forbidden`

#### Scenario: Plugin-catalog passthrough is a protected proxy path
- **WHEN** allowlist contains one or more IP entries and an unlisted client calls `GET /ps/plugins/list` or `GET /plugins/featured`
- **THEN** the request returns HTTP 403 with OpenAI-style error code `ip_forbidden` before any upstream request is made

#### Scenario: Dashboard endpoints are not restricted
- **WHEN** allowlist is active
- **THEN** dashboard endpoints under `/api/*` remain accessible (subject to dashboard auth only)
