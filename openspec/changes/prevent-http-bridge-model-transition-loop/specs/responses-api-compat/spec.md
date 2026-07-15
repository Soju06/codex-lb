# responses-api-compat Delta

## ADDED Requirements

### Requirement: HTTP bridge model-transition isolation is single-pass

When an HTTP bridge request cannot reuse the session selected by its incoming affinity because that session uses an incompatible model, the service MUST preserve the resulting internal model-parallel key until bridge creation or reuse completes. It MUST NOT reapply the original session-header or turn-state fallback to the same request after selecting that fork.

#### Scenario: Fresh turn state falls back to a session on another model

- **GIVEN** a request carries a fresh generated turn-state header and a session header whose active bridge uses an incompatible model
- **WHEN** lookup isolates the request with an internal model-parallel key
- **THEN** lookup emits at most one model-transition fork for that request scope
- **AND** bridge creation continues under the internal key without closing or reusing the incompatible session

#### Scenario: Follow-up fallback has no previous-response lookup

- **GIVEN** a request carries a fresh generated turn-state header, a `previous_response_id` without a local or durable lookup, and a session header whose active bridge uses an incompatible model
- **WHEN** lookup isolates the request with an internal model-parallel key
- **THEN** the session-header fallback remains an anchored continuation for the rest of that lookup/create operation
- **AND** bridge creation continues under the internal key without a `continuity_lost` error

#### Scenario: Full cache preserves the incompatible parent

- **GIVEN** the HTTP bridge cache is at its session limit and a model transition isolates a session-header fallback into a child key
- **WHEN** creation needs to evict an idle session
- **THEN** the incompatible session-header parent MUST NOT be selected for that eviction
- **AND** ordinary LRU eviction remains eligible for other idle sessions

#### Scenario: In-flight parent completes before model isolation

- **GIVEN** a request waits for an in-flight session-header parent whose completed bridge uses an incompatible model
- **WHEN** the request isolates itself with an internal model-parallel key after that wait
- **THEN** the completed parent MUST receive the same capacity-eviction protection as an immediately available parent

#### Scenario: Compatible session fallback remains reusable

- **GIVEN** a request carries a fresh generated turn-state header and a session header whose active bridge uses a compatible model
- **WHEN** lookup applies the session-header fallback
- **THEN** the compatible bridge remains eligible for normal reuse
