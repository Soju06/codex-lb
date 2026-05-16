## Why

The main database pool is configurable and defaults to a higher burst capacity,
but the separate background/request-adjacent pool is hard-coded to
`pool_size=3` and `max_overflow=2`. Some request paths, including auth and proxy
helpers, use that pool during client traffic. Under bursty `/v1/chat/completions`
load this can still surface as SQLAlchemy QueuePool timeout errors even when
the main pool settings are larger.

## What Changes

- add explicit `database_background_pool_size` and `database_background_max_overflow` settings
- default the background pool to the main pool size and overflow values
- keep operators able to lower the background pool separately when they want a smaller auxiliary pool
- document the background pool controls

## Impact

Default SQLite/PostgreSQL deployments get the same burst capacity for
request-adjacent background sessions as the main request pool. Existing
deployments that intentionally want the old smaller pool can configure
`CODEX_LB_DATABASE_BACKGROUND_POOL_SIZE=3` and
`CODEX_LB_DATABASE_BACKGROUND_MAX_OVERFLOW=2`.
