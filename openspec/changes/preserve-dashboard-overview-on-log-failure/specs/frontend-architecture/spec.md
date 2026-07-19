## ADDED Requirements

### Requirement: Dashboard overview and request-log listing fail independently

The Dashboard SHALL gate overview-backed statistics, quota, projections, and account controls only on dashboard overview availability. The Request Logs section SHALL own the initial loading, terminal error, and ready states of its listing query without hiding healthy overview-backed content.

When the initial request-log listing reaches a terminal error, the Request Logs section MUST remain visible, MUST render the listing error inside that section, MUST announce that error through an alert semantic local to the section, and MUST expose a keyboard-operable, accessibly named Retry action. Activating Retry MUST refetch only the request-log listing query and MUST NOT refetch or hide healthy overview-backed content.

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
