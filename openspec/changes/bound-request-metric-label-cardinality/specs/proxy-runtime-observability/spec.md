## ADDED Requirements

### Requirement: Request metric labels have bounded cardinality

The service MUST expose request counter and duration metrics with finite-vocabulary
`method` and `path` labels. The `method` label MUST be one of `GET`, `POST`, `PUT`,
`PATCH`, `DELETE`, `HEAD`, `OPTIONS`, or `OTHER`; any other HTTP method MUST map to
`OTHER`. The `path` label MUST preserve the existing `/v1/...`, `/api/...`, and
`/health/...` collapse values and the existing bare `/health` value. Paths under
`/backend-api/` MUST map to `/backend-api/...`, and paths under `/internal/` MUST map
to `/internal/...`; every other path MUST map to the single `/other` sentinel. Metric
labels MUST NOT contain raw or truncated unmatched paths.

#### Scenario: Unmatched paths share one metric label

- **WHEN** requests use distinct paths outside the `/v1/`, `/api/`, `/health/`,
  `/backend-api/`, and `/internal/` prefixes, including SPA-looking paths
- **THEN** request counter and duration metrics use `path="/other"` for every
  such request
- **AND** no raw unmatched path or truncated unmatched path becomes a metric
  label value

#### Scenario: Primary proxy paths use bounded labels

- **WHEN** requests use `/backend-api/codex/responses`, dynamic
  `/backend-api/files/{file_id}/uploaded`, or `/internal/bridge/...` paths
- **THEN** request counter and duration metrics use `/backend-api/...` for every
  `/backend-api/` path and `/internal/...` for every `/internal/` path
- **AND** no dynamic file ID or other raw suffix becomes a metric label value

#### Scenario: Unsupported methods share the OTHER label

- **WHEN** a request uses an HTTP method outside the supported method vocabulary
- **THEN** request counter and duration metrics use `method="OTHER"`

#### Scenario: Existing collapsed paths remain stable

- **WHEN** a request uses a path under `/v1/`, `/api/`, or `/health/`, or uses bare `/health`
- **THEN** the metric path label retains its existing value
