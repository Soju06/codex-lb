## ADDED Requirements

### Requirement: Operation abandonment is observable

When bridge maintenance moves an ambiguous operation to `abandoned`, the
service MUST emit a structured low-cardinality diagnostic containing the
source state, abandonment reason, bounded age, and owner-lease outcome, and
MUST increment a Prometheus counter labeled only by source state. The
diagnostic and metric MUST NOT contain request text, response IDs, API keys,
account emails, or raw continuity keys.

#### Scenario: stale operation abandonment is diagnosable

- **WHEN** an eligible `unknown` or `acknowledged` operation is abandoned
- **THEN** logs identify the source state and stale-owner reason
- **AND** the abandonment counter increases for that source state
- **AND** no sensitive request or continuity value is emitted
