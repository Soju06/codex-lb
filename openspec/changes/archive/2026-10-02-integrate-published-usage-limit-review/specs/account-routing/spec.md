## ADDED Requirements

### Requirement: Published per-window policies retain reviewed admission boundaries

Enabled duration-matched per-window policies MUST receive the same final admission, dispatch-preparation, telemetry-unavailability, and canonical error guarantees as scalar policies. Integration MUST preserve saved default and override values, duration-matched reserve presentation, and pinned ownership of already-dispatched work.

#### Scenario: An override changes while a bridge turn waits
- **WHEN** an enabled window override becomes blocking during serialized dispatch or durable preparation
- **THEN** the unsent turn is rejected before upstream dispatch with `account_usage_limit_reached`
- **AND** its admission resources are released without changing already-dispatched ownership

#### Scenario: An override-only policy loses telemetry
- **WHEN** a successful poll omits standard windows required by an enabled override-only policy
- **THEN** older observations do not authorize work
- **AND** dashboard state and HTTP Responses use the existing unavailable-policy and canonical denial contracts

#### Scenario: Saved overrides survive integration
- **WHEN** a saved policy has separate default, 5-hour, and weekly thresholds
- **THEN** account updates, routing, and dashboard controls retain those independent values and their duration semantics

### Requirement: Per-window sticky admission observes final invalidation

If selection data changes during affinity persistence for an enabled per-window policy, selection MUST return `selection_state_changed` without an account or concurrency lease and MUST preserve established affinity ownership.

#### Scenario: An override crosses its cap during affinity persistence
- **WHEN** a selected account becomes blocked by a committed override observation during affinity persistence
- **THEN** selection returns no account or lease and releases admission accounting

## MODIFIED Requirements

### Requirement: Responses policy denials retain the canonical error envelope

A local usage-policy denial before upstream dispatch on an HTTP Responses request MUST use HTTP 429 with code `account_usage_limit_reached` and type `rate_limit_error`. This contract MUST apply to direct HTTP streaming and nonstreaming requests as well as bridge admission. Backend SSE requests MUST retain the same error code and type in their terminal event.

#### Scenario: An HTTP Responses request has no policy-eligible account
- **WHEN** a streaming or nonstreaming HTTP Responses request is denied solely by an enabled account usage policy
- **THEN** it returns HTTP 429 with code `account_usage_limit_reached` and type `rate_limit_error`
- **AND** it does not dispatch upstream

### Requirement: Empty successful usage polls supersede capped-account measurements

When a successful usage poll provides no standard quota windows for an account with an enabled usage policy, the system MUST supersede older standard observations with unavailable data and invalidate selection data. It MUST NOT continue authorizing that account from the older measurements. Accounts without an enabled policy MUST persist unavailable placeholders for applicable standard windows while retaining disabled-policy routing semantics.

#### Scenario: A capped account loses standard telemetry
- **GIVEN** an enabled policy has fresh standard observations below its cap
- **WHEN** a successful poll omits the standard rate-limit object or returns no standard windows
- **THEN** the dashboard reports `data_unavailable`
- **AND** account selection rejects it with `account_usage_limit_reached`

#### Scenario: An uncapped account has an additional-only poll
- **WHEN** an account without an enabled usage policy receives a successful poll with no standard windows
- **THEN** the poll records unavailable placeholders for applicable standard windows without introducing a local routing-policy block
