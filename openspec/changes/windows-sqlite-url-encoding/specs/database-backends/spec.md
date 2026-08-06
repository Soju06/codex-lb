# database-backends Delta

## ADDED Requirements

### Requirement: SQLAlchemy-rendered Windows SQLite paths are percent-decoded before opening

When a SQLite database URL is converted to a filesystem path for direct filesystem use (e.g. startup directory creation, startup integrity checks, migration locks, or the usage repository's read-only helper), a path that matches a recognizable SQLAlchemy-rendered Windows form — an encoded drive marker (`<letter>%3A` followed by an encoded or raw path separator) or an encoded UNC prefix (`%5C%5C`) — MUST be percent-decoded before being handed to the filesystem. SQLAlchemy's `URL.render_as_string()` percent-encodes a Windows-style default path (`C:\Users\...` -> `C%3A%5CUsers%5C...`); without decoding, the literal escaped string either fails to open with "unable to open database file" or creates a stray 0-byte database next to the current working directory, which breaks account/usage reads with `no such table`.

Paths that do NOT match those rendered Windows forms MUST be preserved literally. Settings builds the default SQLite URL directly from the configured data directory without URL-encoding it, so a percent sequence in a POSIX or raw Windows path (e.g. `/var/lib/codex%20lb/store.db`) names a real directory and MUST NOT be rewritten by decoding.

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

#### Scenario: Literal percent sequences in POSIX paths are preserved

- **GIVEN** a POSIX SQLite URL whose path contains a literal percent sequence (`sqlite+aiosqlite:////var/lib/codex%20lb/store.db`) built directly from the configured data directory
- **WHEN** the path is extracted for filesystem use or the URL is normalized
- **THEN** the filesystem path remains `/var/lib/codex%20lb/store.db` and the URL is unchanged (the sequence is not decoded to a space)

#### Scenario: Normalized UNC paths keep fragment characters

- **GIVEN** an encoded UNC SQLite URL whose decoded share path contains a legal `#` character (`sqlite:///%5C%5Cserver%5Cshare%23x%5Cstore.db`)
- **WHEN** the URL is normalized and the filesystem path is then extracted from the normalized URL
- **THEN** the extracted path is `\\server\share#x\store.db`
- **AND** the path is not truncated at the `#` as if it were a URL fragment separator

#### Scenario: POSIX paths are unchanged

- **GIVEN** a POSIX-style SQLite URL (`sqlite+aiosqlite:///var/lib/codex-lb/store.db`)
- **WHEN** the path is extracted and decoded
- **THEN** the result is identical to the input path (no `%` to decode; behavior is a no-op)

#### Scenario: In-memory databases are not treated as file paths

- **GIVEN** a `:memory:` SQLite URL
- **WHEN** the path is extracted
- **THEN** no filesystem path is returned and no file is created
