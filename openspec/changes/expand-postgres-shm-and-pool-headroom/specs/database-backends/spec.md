## ADDED Requirements

### Requirement: Default pool sizing preserves raw-slot reserve on default max_connections

The default values of `database_pool_size` and `database_max_overflow` MUST
keep one replica's aggregate application connection capacity —
`(database_pool_size + database_max_overflow) * 2 pooled engines * 1
supported worker` — at or below 80, so a single replica on PostgreSQL's
default `max_connections=100` retains at least 20 raw server slots for
PostgreSQL-reserved connections, the migration path's two-connection peak,
administration, and transient non-application clients.

#### Scenario: Default single replica fits default max_connections

- **WHEN** one replica runs with the default `database_pool_size` and
  `database_max_overflow`
- **THEN** both pooled engines together cap at no more than 80 PostgreSQL
  connections
- **AND** at least 20 raw server slots remain on a default
  `max_connections=100` server

#### Scenario: Operators can still tune the pool

- **WHEN** `CODEX_LB_DATABASE_POOL_SIZE` or `CODEX_LB_DATABASE_MAX_OVERFLOW`
  is set in the environment
- **THEN** the configured values override the defaults for both pooled
  engines
