## ADDED Requirements

### Requirement: Migration CLI distinguishes omitted and empty targets

The `app.db.migrate` / `codex-lb-db` CLI SHALL use the settings-derived database URL only when `--db-url` is omitted. If `--db-url` is explicitly supplied as an exact empty string, the CLI MUST terminate with an argument error before resolving or opening a settings-derived database target. This validation MUST apply to every supported migration subcommand: `upgrade`, `current`, `check`, `wait-for-head`, `wait-for-connection`, and `stamp`.

#### Scenario: Explicit empty target is rejected before side effects

- **GIVEN** settings would resolve a valid database target
- **WHEN** any supported migration subcommand is invoked with `--db-url ""`
- **THEN** the CLI exits nonzero with an argument-validation error
- **AND** it does not select, connect to, create, inspect, migrate, or stamp the settings-derived target

#### Scenario: Omitted target retains the settings fallback

- **GIVEN** settings resolve a valid database target
- **WHEN** a supported migration subcommand is invoked without `--db-url`
- **THEN** the CLI uses the settings-derived database target
