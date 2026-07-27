## ADDED Requirements

### Requirement: Server CLI validates the main listener port before startup

The `codex-lb` server CLI SHALL accept integer main-listener ports in the inclusive range `0..65535` when supplied through `--port` or `PORT`, and an explicit `--port` SHALL continue to take precedence over `PORT`. The CLI SHALL reject non-integer values and integers outside that range before loading Uvicorn, importing or starting the ASGI application, running its lifespan or migrations, or creating runtime data. A rejection MUST identify `--port/PORT`, state the supported range, and include the invalid value.

#### Scenario: Out-of-range command-line port is rejected before startup

- **WHEN** an operator supplies `--port` with an integer below `0` or above `65535`
- **THEN** the CLI exits with an error that identifies `--port/PORT`, the invalid value, and the supported range `0..65535`
- **AND** Uvicorn is not loaded
- **AND** the ASGI lifespan, migrations, and runtime data creation do not run

#### Scenario: Out-of-range environment port is rejected before startup

- **WHEN** `PORT` contains an integer below `0` or above `65535`
- **AND** no `--port` flag is supplied
- **THEN** the CLI exits with an error that identifies `--port/PORT`, the invalid value, and the supported range `0..65535`
- **AND** Uvicorn is not loaded
- **AND** the ASGI lifespan, migrations, and runtime data creation do not run

#### Scenario: Non-integer listener port is rejected before startup

- **WHEN** the selected `--port` or `PORT` value is not an integer
- **THEN** the CLI exits with an error that identifies `--port/PORT` and the invalid value
- **AND** Uvicorn is not loaded

#### Scenario: Inclusive listener-port boundaries are forwarded

- **WHEN** the selected `--port` or `PORT` value is `0` or `65535`
- **THEN** the CLI forwards the same integer to Uvicorn
- **AND** port `0` retains Uvicorn's ephemeral-listener behavior

#### Scenario: Command-line port retains precedence over the environment

- **WHEN** `PORT` contains any value
- **AND** the operator supplies an in-range `--port` value
- **THEN** the CLI validates and forwards the flag value
- **AND** the environment value does not replace it
