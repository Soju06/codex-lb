## ADDED Requirements

### Requirement: File-pin required owner does not rewrite thread locality

A resolved live `input_file.file_id` pin MUST be selected as the required owner without consulting or rewriting the current-Codex thread-scoped soft mapping. The process-session compatibility row MAY still be consulted as independent hard ownership. If that raw row conflicts with the pin account, the request MUST fail closed. A missing process-session preference MAY still initialize insert-if-absent.

#### Scenario: File-pinned request owner overrides thread locality

- **GIVEN** a request carries a `thread-id` whose bounded mapping points to account A
- **AND** its `input_file.file_id` is durably pinned to account B
- **WHEN** the request is routed
- **THEN** account B is treated as the required owner
- **AND** the thread mapping is neither consulted as an owner nor rewritten

#### Scenario: File pin still conflicts with a raw process-session owner

- **GIVEN** a raw process-session `codex_session` row points to account A
- **AND** a live file pin points to account B
- **WHEN** the request is routed
- **THEN** the service fails with `continuity_owner_conflict` before upstream dispatch
- **AND** neither the raw row nor the thread row is rewritten
