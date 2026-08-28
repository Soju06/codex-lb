## ADDED Requirements

### Requirement: PostgreSQL connection budgets include every pooled engine

The application SHALL define its per-worker PostgreSQL connection capacity as the configured per-engine pool capacity multiplied by the declared set of independently pooled engine roles. The request-path and background-task engine creation paths MUST each use the shared role-aware PostgreSQL engine factory, and the engine-count budget MUST be derived from those declared roles. The owned server launcher MUST run the supported one worker per replica explicitly, rather than allowing `WEB_CONCURRENCY` to multiply worker processes and their pools.

#### Scenario: One replica reaches configured pool capacity

- **WHEN** both declared PostgreSQL engine roles in one application worker reach `database_pool_size + database_max_overflow`
- **THEN** the worker's aggregate application connection capacity is `2 * (database_pool_size + database_max_overflow)`
- **AND** both engines were created through the role-aware factory counted by that formula

#### Scenario: WEB_CONCURRENCY cannot multiply owned-launcher pools

- **GIVEN** `WEB_CONCURRENCY` is greater than 1
- **WHEN** the application starts through the owned `app.cli` launcher used by Helm
- **THEN** the launcher explicitly starts one Uvicorn worker
- **AND** the replica creates only the request-path and background-task pools
- **AND** operators MUST scale supported deployments through replicas rather than custom multi-worker launchers

#### Scenario: Test database disables pooling

- **WHEN** `CODEX_LB_TEST_DATABASE_URL` selects `NullPool`
- **THEN** pool sizing controls and the production pooled-engine budget do not apply to that test engine
