# responses-api-compat Delta

## ADDED Requirements

### Requirement: Parked UNKNOWN recovery is bounded and fail-closed

When parked HTTP bridge recovery is enabled, the proxy MUST consider only a
bounded set of recent `UNKNOWN` operations for the same durable session and
model, within the configured creation-age window.  A candidate MUST have an
exact canonical request-body fingerprint match and a nonblank parent response
identifier.  The proxy MUST recover only when exactly one candidate matches;
missing, ambiguous, stale, or conflicting candidates MUST remain fail-closed.

Before replay, the proxy MUST durably rebind the selected operation and fence
its owner.  Recovery MUST remain bounded by the configured attempt limit and
MUST emit low-cardinality diagnostics without logging request or response
payloads.

#### Scenario: One exact recent candidate is replayed

- **GIVEN** parked recovery is enabled for an anchored continuation
- **AND** one recent `UNKNOWN` operation matches the same session, model,
  canonical request body, and nonblank parent response
- **WHEN** the proxy handles the continuation
- **THEN** it durably rebinds and owner-fences that operation before replay
- **AND** it performs at most the configured number of recovery attempts.

#### Scenario: Ambiguous or unverifiable candidates fail closed

- **GIVEN** no candidate, multiple candidates, a stale candidate, a blank
  parent response, or a canonical-body mismatch is observed
- **WHEN** parked recovery is considered
- **THEN** the proxy MUST NOT replay any candidate
- **AND** it MUST return the existing continuity failure path with only
  low-cardinality diagnostics.
