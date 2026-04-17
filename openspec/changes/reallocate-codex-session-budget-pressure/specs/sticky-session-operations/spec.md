## MODIFIED Requirements
### Requirement: Sticky sessions are explicitly typed
The system SHALL persist each sticky-session mapping with an explicit kind so durable Codex backend affinity, durable dashboard sticky-thread routing, and bounded prompt-cache affinity can be managed independently.

#### Scenario: Backend Codex session affinity is stored as durable
- **WHEN** a backend Codex request creates or refreshes stickiness from `session_id`
- **THEN** the stored mapping kind is `codex_session`

#### Scenario: Backend Codex session rebinds under budget pressure
- **WHEN** a backend Codex request resolves an existing `codex_session` mapping
- **AND** the pinned account is above the configured sticky reallocation budget threshold
- **AND** another eligible account remains below that threshold
- **THEN** selection rebinds the durable `codex_session` mapping to the healthier account before sending the request upstream
