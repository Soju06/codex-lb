## ADDED Requirements

### Requirement: Dashboard status separates service readiness from usage synchronization

The fixed dashboard status bar MUST render independent `Service ready` and
`Usage synced` signals. `Service ready` MUST use the existing `/health/ready`
response and MUST treat a failed request or a non-`ok` status as not ready.
`Usage synced` MUST remain derived only from the dashboard overview
`lastSyncAt` value and MUST be fresh only while that timestamp is less than 60
seconds old. The service-readiness signal MUST NOT use upstream account or
provider health. The dashboard layout MUST reserve at least the status bar's
rendered height so wrapped status rows do not cover page content.

#### Scenario: Ready service with stale usage

- **WHEN** `/health/ready` returns `status: "ok"`
- **AND** `lastSyncAt` is absent or at least 60 seconds old
- **THEN** the status bar shows the service as ready
- **AND** independently shows usage as stale

#### Scenario: Unready service with fresh usage

- **WHEN** `/health/ready` fails or returns a non-`ok` status
- **AND** `lastSyncAt` is less than 60 seconds old
- **THEN** the status bar shows the service as not ready
- **AND** independently shows usage as synced

#### Scenario: Readiness is still being checked

- **WHEN** the initial `/health/ready` request has not completed
- **THEN** the service-readiness signal shows a checking state
- **AND** the usage synchronization signal remains independently derived from
  `lastSyncAt`

#### Scenario: Status signals wrap onto additional rows

- **WHEN** the fixed status bar grows because its signals wrap
- **THEN** the dashboard updates its reserved bottom space to the rendered
  status-bar height
- **AND** the fixed status bar does not cover page content
