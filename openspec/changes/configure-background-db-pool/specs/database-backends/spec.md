## MODIFIED Requirements

### Requirement: Database pool controls cover request-adjacent background sessions
The service SHALL expose database pool settings for both the main request pool
and the background/request-adjacent session pool. The background pool SHALL
default to the main pool size and overflow settings, and operators MAY override
the background pool size and overflow separately.

#### Scenario: Background pool inherits main pool capacity
- **WHEN** `database_background_pool_size` and `database_background_max_overflow` are unset
- **THEN** the background/request-adjacent DB pool uses `database_pool_size` and `database_max_overflow`

#### Scenario: Background pool has explicit lower capacity
- **WHEN** `database_background_pool_size` and `database_background_max_overflow` are configured
- **THEN** the background/request-adjacent DB pool uses those explicit values
