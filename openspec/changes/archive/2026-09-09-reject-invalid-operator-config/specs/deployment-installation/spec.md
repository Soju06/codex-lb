## ADDED Requirements

### Requirement: Operator metrics and log configuration fails closed

The application MUST accept `CODEX_LB_METRICS_PORT` only in inclusive
`1..65535` and `CODEX_LB_LOG_FORMAT` only as `text` or `json`. Invalid values
MUST produce field-specific validation errors before metrics startup or
formatter selection. Existing main/metrics collision rejection MUST remain.

Helm values schema MUST enforce the same metrics range and log-format set before
rendering/install. Valid defaults/boundaries MUST remain unchanged.

#### Scenario: Impossible metrics port is rejected

- **WHEN** metrics port is zero, negative, or above 65535
- **THEN** settings validation identifies `metrics_port`

#### Scenario: Unknown log format is rejected

- **WHEN** log format is not text or json
- **THEN** settings validation identifies `log_format`

#### Scenario: Helm rejects invalid operator values

- **WHEN** Helm metrics/log values violate the same contract
- **THEN** schema validation fails with the values path
