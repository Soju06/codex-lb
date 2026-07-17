# Tasks: pin-asyncpg-session-timezone-utc

- [x] Add a `database-backends` requirement that PostgreSQL asyncpg sessions
      MUST pin the database session time zone to UTC.
- [x] Configure `postgresql+asyncpg://` engine connect args with
      `server_settings.timezone=UTC`, preserving existing test-only prepared
      statement cache tuning.
- [x] Add unit coverage for the PostgreSQL connect args and engine kwargs.
- [x] Add PostgreSQL integration coverage proving a new app-configured asyncpg
      engine reports `SHOW TIME ZONE = UTC` even when the database default is
      temporarily non-UTC.
- [x] Run focused unit/integration tests and `openspec validate --specs`.
