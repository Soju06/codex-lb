## ADDED Requirements

### Requirement: CLI HTTP listener defaults to remote-capable binding

The CLI server entrypoint and packaged Docker entrypoint MUST bind the main HTTP listener to `0.0.0.0` by default so Docker, VM, and remote-dashboard deployments are reachable through a published or firewall-opened TCP `2455` port. The CLI MUST allow operators to force local-only binding with `--host 127.0.0.1` or `CODEX_LB_HOST=127.0.0.1`. The Docker entrypoint MUST honor `CODEX_LB_HOST` and `PORT` while keeping `0.0.0.0:2455` as its default. When both `CODEX_LB_HOST` and the generic `HOST` environment variable are set, `CODEX_LB_HOST` MUST take precedence. When `--host` is provided, it MUST take precedence over environment defaults.

#### Scenario: Direct CLI run is reachable through a published remote port

- **WHEN** an operator starts `codex-lb` without a host override
- **THEN** the server binds the main HTTP listener to `0.0.0.0`
- **AND** remote reachability is controlled by the operating-system firewall, cloud security group, or outer publish layer rather than by a loopback-only app bind

#### Scenario: Local-only bind remains available

- **WHEN** an operator starts `codex-lb --host 127.0.0.1`
- **THEN** the server binds the main HTTP listener to `127.0.0.1`

#### Scenario: Project-specific host env wins

- **GIVEN** `CODEX_LB_HOST=0.0.0.0`
- **AND** `HOST=127.0.0.1`
- **WHEN** an operator starts `codex-lb`
- **THEN** the server binds the main HTTP listener to `0.0.0.0`
