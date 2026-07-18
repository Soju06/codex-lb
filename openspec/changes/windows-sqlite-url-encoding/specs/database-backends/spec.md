# database-backends Delta

## ADDED Requirements

### Requirement: SQLite file paths are percent-decoded before opening

When a SQLite database URL is converted to a filesystem path for direct filesystem use (e.g. startup directory creation, startup integrity checks, migration locks, or the usage repository's read-only helper), the path MUST be percent-decoded before being handed to the filesystem. SQLAlchemy's `URL.render_as_string()` percent-encodes a Windows-style default path (`C:\Users\...` -> `C%3A%5CUsers%5C...`); without decoding, the literal escaped string either fails to open with "unable to open database file" or creates a stray 0-byte database next to the current working directory, which breaks account/usage reads with `no such table`.

#### Scenario: Windows default path resolves to the real file

- **GIVEN** the default SQLite URL on Windows (`sqlite+aiosqlite:///C:\Users\...\store.db`)
- **WHEN** `URL.render_as_string()` percent-encodes it into `sqlite:///C%3A%5CUsers%5C...%5Cstore.db`
- **AND** the path is extracted and decoded
- **THEN** `sqlite3.connect()` receives `C:\Users\...\store.db` (the real file), not the percent-escaped literal

#### Scenario: Startup uses the decoded SQLite path

- **GIVEN** a percent-encoded SQLite file URL whose decoded parent directory differs from the percent-literal parent
- **WHEN** `init_db()` prepares the SQLite directory and runs the startup integrity check
- **THEN** the decoded parent directory is created
- **AND** the integrity check receives the decoded database path

#### Scenario: POSIX paths are unchanged

- **GIVEN** a POSIX-style SQLite URL (`sqlite+aiosqlite:///var/lib/codex-lb/store.db`)
- **WHEN** the path is extracted and decoded
- **THEN** the result is identical to the input path (no `%` to decode; behavior is a no-op)

#### Scenario: In-memory databases are not treated as file paths

- **GIVEN** a `:memory:` SQLite URL
- **WHEN** the path is extracted
- **THEN** no filesystem path is returned and no file is created
