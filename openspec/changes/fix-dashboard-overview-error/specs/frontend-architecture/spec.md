## MODIFIED Requirements

### Requirement: Dashboard overview and request-log listing fail independently

The Dashboard SHALL gate overview-backed statistics, quota, projections, and account controls only on dashboard overview availability. The Request Logs section SHALL own the initial loading, terminal error, and ready states of its listing query without hiding healthy overview-backed content.

When the initial request-log listing reaches a terminal error, the Request Logs section MUST remain visible, MUST render the listing error inside that section, MUST announce that error through an alert semantic local to the section, and MUST expose a keyboard-operable, accessibly named Retry action. Activating Retry MUST refetch only the request-log listing query and MUST NOT refetch or hide healthy overview-backed content.

While the initial overview request is pending with no data, the Dashboard SHALL
render the existing page-wide skeleton. When that request reaches a terminal
error with no data, it MUST NOT render the skeleton; it MUST preserve the shell,
MUST announce the error, and MUST expose a keyboard-operable Retry.

Retry SHALL refetch only the overview query. The terminal error SHALL remain
rendered and Retry SHALL remain disabled with a busy state while that no-data
refetch is in flight. Successful refetch SHALL replace the error with overview
content. Cached overview data SHALL remain visible on later refetch errors.

#### Scenario: Initial request-log failure preserves healthy overview

- **GIVEN** dashboard overview, projections, and request-log filter options load successfully
- **WHEN** the initial request-log listing reaches a terminal error
- **THEN** overview statistics, quota, and account content remain rendered
- **AND** the page-wide Dashboard loading skeleton is not rendered
- **AND** the Request Logs section contains and announces the listing error and exposes a Retry action

#### Scenario: Request-log retry recovers independently

- **GIVEN** healthy overview-backed content is rendered and the initial request-log listing has failed
- **WHEN** the listing endpoint recovers and the operator activates Retry
- **THEN** only the request-log listing query is refetched
- **AND** healthy overview-backed content remains visible throughout recovery
- **AND** the recovered request-log rows render in the Request Logs section

#### Scenario: Request logs load inside their section

- **GIVEN** dashboard overview data is available
- **WHEN** the initial request-log listing is still pending
- **THEN** overview-backed content is rendered
- **AND** the Request Logs section renders its own loading state
- **AND** the page-wide Dashboard loading skeleton is not rendered

#### Scenario: Initial overview loading keeps the existing page skeleton

- **WHEN** the dashboard overview is not yet available
- **THEN** the Dashboard renders its existing page-wide loading skeleton
- **AND** it does not render overview-backed content prematurely

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
