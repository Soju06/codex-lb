## MODIFIED Requirements

### Requirement: Route metadata must be persisted for migrated upstream calls

Request logs for migrated upstream calls MUST record route mode, proxy pool
id, proxy endpoint id, same-pool fallback use, and fail-closed reason where
applicable. The request-log API and dashboard request details MUST expose
those credential-safe values to operators and MUST NOT expose proxy
credentials.

#### Scenario: Fail-closed route is diagnosable from request details

- **GIVEN** route resolution fails closed before network open
- **AND** the request log records the route mode and fail-closed reason
- **WHEN** an operator opens that request in the dashboard
- **THEN** the request details show the recorded route mode and fail-closed
  reason
- **AND** no proxy credentials are included

#### Scenario: Successful routed request exposes its selected route

- **GIVEN** a request log records a proxy pool id, proxy endpoint id, and
  same-pool fallback use
- **WHEN** the request-log API returns that row
- **THEN** all three values are present unchanged
