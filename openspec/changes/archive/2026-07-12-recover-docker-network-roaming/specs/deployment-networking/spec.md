## ADDED Requirements

### Requirement: Stock Docker launches use embedded DNS

The documented standalone Docker deployment MUST attach codex-lb to a user-defined bridge network, and stock Compose deployments MUST declare a user-defined default bridge. The stock configuration MUST NOT hard-code a public recursive DNS server.

#### Scenario: Standalone quick start uses a user-defined bridge

- **WHEN** an operator follows the documented standalone Docker quick start
- **THEN** the instructions create the codex-lb bridge idempotently
- **AND** start the container with that bridge selected by `--network`

#### Scenario: Compose uses a user-defined default bridge

- **WHEN** Docker Compose renders either stock Compose deployment
- **THEN** the server is attached to a user-defined default bridge
- **AND** the rendered service does not pin a public DNS server
