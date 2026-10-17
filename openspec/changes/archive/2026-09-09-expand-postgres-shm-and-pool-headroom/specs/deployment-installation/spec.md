## ADDED Requirements

### Requirement: Compose Postgres service sizes /dev/shm for parallel query

The Docker Compose `postgres` service MUST set an explicit `shm_size` of at
least 1GB. Docker's default 64MB `/dev/shm` causes PostgreSQL parallel
workers to fail with `could not resize shared memory segment ... No space
left on device` once a parallel hash join spills past the segment.

#### Scenario: Compose postgres service pins shm_size

- **WHEN** `docker-compose.yml` is inspected
- **THEN** the `postgres` service declares `shm_size` of at least 1GB

#### Scenario: Parallel hash join spills past 64MB

- **GIVEN** the Compose `postgres` service is running with the declared
  `shm_size`
- **WHEN** a parallel hash join spills more than 64MB of build tuples into
  dynamic shared memory
- **THEN** the query does not fail with `could not resize shared memory
  segment`
