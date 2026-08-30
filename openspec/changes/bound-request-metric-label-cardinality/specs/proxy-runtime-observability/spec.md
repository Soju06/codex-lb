## ADDED Requirements

### Requirement: Request metric labels have bounded cardinality

The service MUST expose request counter and duration metrics with finite-vocabulary
`method` and `path` labels. The `method` label MUST be one of `GET`, `POST`, `PUT`,
`PATCH`, `DELETE`, `HEAD`, `OPTIONS`, or `OTHER`; any other HTTP method MUST map to
`OTHER`. The `path` label MUST preserve the existing `/v1/...`, `/api/...`, and
`/health/...` collapse values and the existing bare `/health` value, while every
other path MUST map to the single `/other` sentinel. Metric labels MUST NOT contain
raw or truncated unmatched paths.

#### Scenario: Unmatched paths share one metric label

- **WHEN** requests use distinct paths outside the `/v1/`, `/api/`, and `/health/`
  prefixes, including SPA-looking paths
- **THEN** request counter and duration metrics use `path="/other"` for every
  such request
- **AND** no raw unmatched path or truncated unmatched path becomes a metric
  label value

#### Scenario: Unsupported methods share the OTHER label

- **WHEN** a request uses an HTTP method outside the supported method vocabulary
- **THEN** request counter and duration metrics use `method="OTHER"`

#### Scenario: Existing collapsed paths remain stable

- **WHEN** a request uses a path under `/v1/`, `/api/`, or `/health/`, or uses bare `/health`
- **THEN** the metric path label retains its existing value
