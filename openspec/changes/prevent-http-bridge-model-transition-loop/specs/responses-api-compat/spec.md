# responses-api-compat Delta

## ADDED Requirements

### Requirement: HTTP bridge model-transition isolation is single-pass

When an HTTP bridge request cannot reuse the session selected by its incoming affinity because that session uses an incompatible model, the service MUST preserve the resulting internal model-parallel key until bridge creation or reuse completes. It MUST NOT reapply the original session-header or turn-state fallback to the same request after selecting that fork.

#### Scenario: Fresh turn state falls back to a session on another model

- **GIVEN** a request carries a fresh generated turn-state header and a session header whose active bridge uses an incompatible model
- **WHEN** lookup isolates the request with an internal model-parallel key
- **THEN** lookup emits at most one model-transition fork for that request scope
- **AND** bridge creation continues under the internal key without closing or reusing the incompatible session

#### Scenario: Compatible session fallback remains reusable

- **GIVEN** a request carries a fresh generated turn-state header and a session header whose active bridge uses a compatible model
- **WHEN** lookup applies the session-header fallback
- **THEN** the compatible bridge remains eligible for normal reuse
