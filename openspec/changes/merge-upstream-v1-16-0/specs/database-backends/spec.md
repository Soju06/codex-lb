## ADDED Requirements

### Requirement: Background database pool sizing is merged safely

The upstream background database pool sizing changes MUST be adopted without regressing request-path database sessions or local SQLite behavior.

#### Scenario: Background jobs use bounded pool behavior

- **WHEN** background usage refresh or quota recovery work runs under burst traffic
- **THEN** it uses the merged background pool behavior
- **AND** request-path sessions remain isolated from avoidable background pool starvation
