## ADDED Requirements

### Requirement: Asyncpg PostgreSQL sessions pin time zone to UTC

When `database_url` resolves to a PostgreSQL backend through the asyncpg driver, the application MUST configure each SQLAlchemy async engine connection with a database session time zone of `UTC`.

This requirement applies to the request-path `engine`, the optional background
`_background_engine`, and any app-created PostgreSQL async engine that uses the
shared PostgreSQL engine kwargs helper.

#### Scenario: Asyncpg sessions ignore non-UTC database defaults

- **GIVEN** `database_url` uses `postgresql+asyncpg://`
- **AND** the PostgreSQL role, database, container, or server default time zone
  is not UTC
- **WHEN** the application opens a new asyncpg connection through its engine
  configuration
- **THEN** `SHOW TIME ZONE` on that connection reports `UTC`
- **AND** naive UTC datetimes written by the application are interpreted as UTC
  before PostgreSQL stores them in `timestamptz` columns

#### Scenario: SQLite backends are not affected

- **GIVEN** `database_url` resolves to a SQLite backend
- **WHEN** the application creates its async engine
- **THEN** PostgreSQL asyncpg `server_settings` are not configured
- **AND** existing SQLite PRAGMAs, busy timeout, and pooling behavior remain
  unchanged
