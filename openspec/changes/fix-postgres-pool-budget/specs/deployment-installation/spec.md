## ADDED Requirements

### Requirement: Helm PostgreSQL capacity guidance accounts for both application pools

Helm sizing documentation and production-oriented values SHALL calculate maximum application PostgreSQL connections as `(databasePoolSize + databaseMaxOverflow) * 2 pooled engines * 1 supported worker * maxReplicas`. Values described as fitting PostgreSQL's default `max_connections=100` MUST reserve at least 20 raw server slots for PostgreSQL-reserved connections, the migration path's two-connection peak, administration, and transient non-application clients.

#### Scenario: Default chart reaches its HPA ceiling

- **WHEN** the default chart scales to `autoscaling.maxReplicas`
- **THEN** both application pools across all replicas require no more than 80 PostgreSQL connections
- **AND** at least 20 raw server slots remain outside the application-pool budget

#### Scenario: Production overlay reaches its HPA ceiling

- **WHEN** `values-prod.yaml` scales to `autoscaling.maxReplicas`
- **THEN** both application pools across all replicas require no more than 80 PostgreSQL connections
- **AND** at least 20 raw server slots remain available for PostgreSQL reservations, migrations, administration, and transient non-application clients
