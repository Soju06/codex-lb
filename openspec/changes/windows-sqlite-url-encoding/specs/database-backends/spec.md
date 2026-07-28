# database-backends Delta

## ADDED Requirements

### Requirement: SQLite file paths are percent-decoded before opening

When a SQLite database URL is converted to a filesystem path for direct filesystem use (e.g. startup directory creation, startup integrity checks, migration locks, or the usage repository's read-only helper), the path MUST be percent-decoded before being handed to the filesystem. SQLAlchemy's `URL.render_as_string()` percent-encodes a Windows-style default path (`C:\Users\...` -> `C%3A%5CUsers%5C...`); without decoding, the literal escaped string either fails to open with "unable to open database file" or creates a stray 0-byte database next to the current working directory, which breaks account/usage reads with `no such table`.

#### Scenario: Windows default path resolves to the real file

- **GIVEN** the default SQLite URL on Windows (`sqlite+aiosqlite:///C:\Users\...\store.db`)
- **WHEN** `URL.render_as_string()` percent-encodes it into `sqlite:///C%3A%5CUsers%5C...%5Cstore.db`
- **AND** the path is extracted and decoded
- **THEN** `sqlite3.connect()` receives `C:\Users\...\store.db` (the real file), not the percent-escaped literal

#### Scenario: Encoded drive with URL slash separators resolves to the real file

- **GIVEN** a Windows SQLite URL with an encoded drive colon and normal URL path separators (`sqlite:///C%3A/Users/me/.codex-lb/store.db`)
- **WHEN** the path is extracted and decoded
- **THEN** the filesystem path is `C:/Users/me/.codex-lb/store.db`, not the literal `C%3A/Users/me/.codex-lb/store.db`

#### Scenario: Startup uses the decoded SQLite path

- **GIVEN** a percent-encoded SQLite file URL whose decoded parent directory differs from the percent-literal parent
- **WHEN** `init_db()` prepares the SQLite directory and runs the startup integrity check
- **THEN** the decoded parent directory is created
- **AND** the integrity check receives the decoded database path

#### Scenario: URL normalization preserves decoded Windows path characters

- **GIVEN** an encoded Windows SQLite URL whose decoded database path contains spaces, literal `%`, or `#`
- **WHEN** the URL is normalized for SQLAlchemy consumers
- **THEN** the returned URL contains the real decoded Windows filesystem path
- **AND** filesystem extraction from that normalized URL returns the same decoded path
- **AND** a raw Windows URL containing a literal percent sequence such as `%23` is not decoded unless it first matched a SQLAlchemy-rendered encoded Windows form

#### Scenario: POSIX paths are unchanged

- **GIVEN** a POSIX-style SQLite URL (`sqlite+aiosqlite:///var/lib/codex-lb/store.db`)
- **WHEN** the path is extracted and decoded
- **THEN** the result is identical to the input path (no `%` to decode; behavior is a no-op)

#### Scenario: In-memory databases are not treated as file paths

- **GIVEN** a `:memory:` SQLite URL
- **WHEN** the path is extracted
- **THEN** no filesystem path is returned and no file is created
