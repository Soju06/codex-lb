## Why

File-backed SQLite deployments can retain idle file descriptors when background
refresh loops use SQLAlchemy's default async SQLite queue pool and keep
`AsyncSession` objects open across upstream network I/O. Long-running usage,
model-registry, and reset-credits refreshes can then accumulate open database,
WAL, and socket descriptors until the host approaches file descriptor pressure.

## What Changes

- Use `NullPool` for file-backed SQLite main and background engines while
  preserving `:memory:` handling and SQLite PRAGMAs.
- Treat database pool size/overflow controls as pooled-backend controls; they do
  not apply to file-backed SQLite engines.
- Add a shared manual-session close helper that rolls back active transactions
  and shields session close from cancellation.
- Refactor usage, model-registry, and reset-credits refresh scheduling so
  account/usage/settings reads happen in short sessions and upstream fetches run
  after those sessions have closed.

## Impact

- **SQLite FD pressure**: file-backed SQLite no longer holds idle pooled DB/WAL
  descriptors, and refresh loops no longer retain read sessions while waiting on
  external APIs.
- **PostgreSQL**: pool behavior and pool controls are unchanged.
- **API compatibility**: no response schema or external API behavior changes.
