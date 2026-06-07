## MODIFIED Requirements
### Requirement: Sticky sessions are explicitly typed
The system SHALL persist each sticky-session mapping with an explicit kind so durable Codex backend affinity, durable dashboard sticky-thread routing, and bounded prompt-cache affinity can be managed independently.

#### Scenario: Dashboard sticky thread routing is stored as durable
- **WHEN** sticky-thread routing creates or refreshes stickiness from a prompt-derived key
- **THEN** the stored mapping kind is `sticky_thread`

#### Scenario: Dashboard sticky thread rebinds under budget pressure
- **WHEN** a request resolves an existing `sticky_thread` mapping
- **AND** the pinned account is otherwise eligible to serve traffic
- **AND** the pinned account is strictly above the configured sticky reallocation budget threshold
- **AND** another eligible account remains at or below that threshold
- **THEN** selection rebinds the durable `sticky_thread` mapping to the healthier account before sending the request upstream

#### Scenario: Dashboard sticky thread is preserved when every candidate is above the threshold
- **WHEN** a request resolves an existing `sticky_thread` mapping
- **AND** the pinned account is otherwise eligible to serve traffic
- **AND** the pinned account is strictly above the configured sticky reallocation budget threshold
- **AND** every other eligible account is also strictly above that threshold
- **THEN** selection retains the existing pinned account to avoid sticky-pin thrashing
