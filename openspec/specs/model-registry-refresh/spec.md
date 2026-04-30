# model-registry-refresh Specification

## Purpose
TBD - created by archiving change harden-upstream-403-recovery. Update Purpose after archive.
## Requirements
### Requirement: Model registry refresh cools down repeated auth-like failures

Background model-registry refresh MUST apply a cooldown to accounts that fail model fetch with ambiguous `401` or `403` responses. Accounts inside that cooldown window MUST be skipped by later model-registry refresh cycles until the cooldown expires or a later successful model fetch clears it.

#### Scenario: Ambiguous model fetch 403 enters cooldown

- **WHEN** model-registry refresh receives an HTTP `403` that does not match a permanent deactivation signal
- **THEN** the account is not deactivated immediately
- **AND** later refresh cycles skip that account until the model-refresh cooldown expires or a successful model fetch clears it

#### Scenario: Successful model fetch clears model-refresh cooldown

- **WHEN** a later model fetch succeeds for an account that was previously cooled down
- **THEN** the model-refresh cooldown is cleared for that account
- **AND** normal model-registry refresh cadence resumes for that account

### Requirement: Model registry refresh preserves the last good snapshot and emits attributed failures

When model-registry refresh cannot fetch fresh model metadata for a candidate account, the service MUST preserve the last successful registry snapshot and MUST log the account, plan, status code, upstream request identifier when present, and a bounded upstream response preview.

#### Scenario: Failed refresh keeps the last good model registry snapshot

- **WHEN** model-registry refresh cannot fetch a fresh model list for any candidate account in a plan
- **THEN** the service keeps serving the last successful model-registry snapshot for that plan instead of replacing it with an empty result

#### Scenario: Model fetch failure log includes request attribution

- **WHEN** model-registry refresh receives an upstream `401` or `403`
- **THEN** the failure log includes the account id, plan, HTTP status, and upstream request identifier when the upstream provided one
- **AND** the log includes only a bounded preview of the upstream response body

