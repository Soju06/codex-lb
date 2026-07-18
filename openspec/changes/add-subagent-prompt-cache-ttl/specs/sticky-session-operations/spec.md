## ADDED Requirements

### Requirement: Subagent prompt-cache affinity is optional

The system SHALL identify subagent requests by a nonblank `x-parent-session-id`, `x-openai-subagent`, or `x-codex-parent-thread-id` header. The dashboard setting `http_responses_session_bridge_subagent_prompt_cache_ttl_seconds` SHALL use `NULL` as the default No Cache mode. When set to a positive number, it SHALL retain the subagent's bridge session, stream lease, and PROMPT_CACHE mapping for that many seconds before closing. When set to `0` or `NULL`, the bridge session, stream lease, and any mapping MUST be released immediately when the subagent response stream ends.

#### Scenario: Subagent uses No Cache by default

- **GIVEN** an incoming request carries `x-parent-session-id`
- **AND** the subagent prompt-cache TTL setting is `NULL` or `0`
- **WHEN** the HTTP bridge selects an account
- **THEN** it MUST NOT read or write a PROMPT_CACHE sticky mapping for the subagent
- **AND** the bridge session and stream lease MUST be released when the response stream ends

#### Scenario: Subagent retains bridge session for configured TTL

- **GIVEN** an incoming request carries `x-parent-session-id`
- **AND** the subagent prompt-cache TTL setting is a positive number
- **WHEN** the HTTP bridge selects an account
- **THEN** the subagent's PROMPT_CACHE mapping MAY be read or written
- **AND** the bridge session and stream lease MUST be retained for the configured TTL duration
- **AND** the bridge session and stream lease MUST be released after the TTL expires

#### Scenario: Canonical session retains standard PROMPT_CACHE behavior

- **GIVEN** an incoming request does not carry `x-parent-session-id`
- **WHEN** the session is created
- **THEN** the session uses the standard affinity-based idle TTL (PROMPT_CACHE/CODEX_SESSION/base)
- **AND** the subagent setting does not affect the canonical session

### Requirement: Completed subagent sessions release resources after TTL

The system MUST release the stream lease and close the HTTP bridge session for a subagent after its configured TTL expires. When the TTL is `NULL` or `0`, the system MUST release immediately. The system MUST NOT delete the sticky mapping for the canonical parent session.

#### Scenario: Subagent with positive TTL releases after delay

- **GIVEN** an HTTP bridge session was marked as a subagent session from `x-parent-session-id`
- **AND** the subagent prompt-cache TTL is a positive number
- **WHEN** its response stream ends
- **THEN** the bridge session and stream lease MUST be retained for the TTL duration
- **AND** after the TTL expires, the session's stream lease MUST be released and the bridge session closed
- **AND** the parent session's sticky mapping remains available

#### Scenario: Subagent with zero TTL releases immediately

- **GIVEN** an HTTP bridge session was marked as a subagent session from `x-parent-session-id`
- **AND** the subagent prompt-cache TTL is `NULL` or `0`
- **WHEN** its response stream ends
- **THEN** the session's stream lease is released immediately
- **AND** the parent session's sticky mapping remains available

### Requirement: Unanchored parallel forks release stream resources after completion

The system MUST close an `internal_unanchored_parallel` HTTP bridge session when its response stream ends. The system MUST release its stream lease at the same time. The parent session's bridge session and sticky mapping MUST remain available.

#### Scenario: Normal parent parallel fork does not retain a stream lease

- **GIVEN** an HTTP bridge creates an `internal_unanchored_parallel` fork for a parent session request
- **WHEN** the fork's response stream ends
- **THEN** the fork bridge session MUST close immediately
- **AND** the fork's stream lease MUST be released
- **AND** the parent bridge session MUST remain available

### Requirement: Sticky session entries expose subagent marker

The sticky sessions API response SHALL include an `is_subagent` boolean field on each entry. The dashboard SHALL display "Prompt cache, Subagent" for prompt-cache entries where `is_subagent` is true. Non-subagent entries SHALL display "Prompt cache" unchanged.

#### Scenario: Subagent entry shows subagent label

- **GIVEN** a sticky session entry exists with `is_subagent=true` and `kind=prompt_cache`
- **WHEN** the dashboard renders the sticky sessions list
- **THEN** the entry SHALL be labelled "Prompt cache, Subagent"

#### Scenario: Parent entry shows standard label

- **GIVEN** a sticky session entry exists with `is_subagent=false` and `kind=prompt_cache`
- **WHEN** the dashboard renders the sticky sessions list
- **THEN** the entry SHALL be labelled "Prompt cache"

### Requirement: Cleanup scheduler applies subagent TTL independently

The background cleanup scheduler SHALL purge subagent prompt-cache mappings using the subagent TTL, independently from parent prompt-cache mappings. The scheduler interval SHALL be capped at 30 seconds so that subagent mappings with short TTLs are cleaned promptly.

#### Scenario: Subagent mappings purged by subagent TTL

- **GIVEN** subagent prompt-cache mappings exist with `is_subagent=true`
- **AND** the subagent prompt-cache TTL is 30 seconds
- **WHEN** the cleanup scheduler runs
- **THEN** subagent mappings older than 30 seconds SHALL be purged
- **AND** parent prompt-cache mappings SHALL NOT be purged by the subagent cutoff
