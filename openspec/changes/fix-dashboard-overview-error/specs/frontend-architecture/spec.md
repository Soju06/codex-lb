## MODIFIED Requirements

### Requirement: Dashboard overview and request-log listing fail independently

The Dashboard SHALL gate overview-backed content only on dashboard overview
availability. While the initial overview request is pending with no data, it
SHALL render the existing page-wide skeleton. When that request reaches a
terminal error with no data, it MUST NOT render the skeleton; it MUST preserve
the shell, MUST announce the error, and MUST expose a keyboard-operable Retry.

Retry SHALL refetch only the overview query. The terminal error SHALL remain
rendered and Retry SHALL remain disabled with a busy state while that no-data
refetch is in flight. Successful refetch SHALL replace the error with overview
content. Cached overview data SHALL remain visible on later refetch errors.

#### Scenario: Terminal overview failure replaces the skeleton

- **GIVEN** no overview data is available
- **WHEN** the overview query reaches terminal error
- **THEN** shell landmarks remain mounted
- **AND** the loading skeleton is removed
- **AND** an alert and keyboard-operable Retry are rendered

#### Scenario: Retry remains visible while fetching

- **GIVEN** the terminal no-data error is rendered
- **WHEN** the operator activates Retry
- **THEN** only the overview query refetches
- **AND** the error remains visible
- **AND** Retry is disabled and exposes a busy state

#### Scenario: Retry recovers in place

- **WHEN** the endpoint succeeds after Retry
- **THEN** overview content replaces the error without a full page reload

#### Scenario: Cached overview survives later failure

- **GIVEN** overview content already exists
- **WHEN** a later refetch fails
- **THEN** the content remains visible without a page-wide skeleton
