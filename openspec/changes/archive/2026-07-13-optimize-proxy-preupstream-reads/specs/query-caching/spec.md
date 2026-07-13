# query-caching Delta

## ADDED Requirements

### Requirement: Proxy API-key auth caching is invalidation-driven with a TTL backstop

The proxy API-key auth cache MUST be invalidated through the cross-instance cache-invalidation mechanism on every key mutation (create/update/delete/reassignment), and its TTL MUST be at least 60 seconds so interactive request turns do not re-read unchanged key rows from the database.

#### Scenario: Key mutations invalidate cached auth promptly

- **GIVEN** an API key validated and cached on an instance
- **WHEN** the key is updated or deleted (on any instance)
- **THEN** the mutation MUST bump the api_key invalidation namespace
- **AND** cached auth data for that key MUST be cleared via the poller, independent of the TTL

#### Scenario: Unchanged keys are served from cache across interactive turns

- **GIVEN** a key validated less than 60 seconds ago with no intervening mutation
- **WHEN** another request authenticates with the same key
- **THEN** validation MUST be served from the cache without a database read

### Requirement: Sticky-session upsert completes in one statement

Sticky-session upserts on the request path MUST persist and return the row with a single `INSERT ... ON CONFLICT ... RETURNING` statement, with unchanged row contents and `updated_at` semantics.

#### Scenario: Upsert issues no follow-up selects

- **WHEN** a sticky session is created or re-affirmed
- **THEN** the repository MUST execute exactly one data statement (the returning upsert) plus the commit
- **AND** the returned row MUST reflect the persisted state for both the insert and update arms

### Requirement: Selection-input reads never run concurrently on a shared session

Account-selection input loading MUST NOT execute multiple statements concurrently on one `AsyncSession`.

#### Scenario: Usage window reads execute sequentially

- **WHEN** selection inputs load primary, secondary, and monthly usage windows
- **THEN** the three reads MUST be awaited sequentially on the shared session
