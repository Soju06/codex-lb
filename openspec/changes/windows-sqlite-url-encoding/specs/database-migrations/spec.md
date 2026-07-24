# database-migrations Delta

## ADDED Requirements

### Requirement: Alembic Config escapes percent characters in the SQLAlchemy URL

When the application builds an Alembic `Config` for migration inspection or upgrade (`_build_alembic_config`), any `%` in the SQLAlchemy URL MUST be escaped to `%%` before being stored via `set_main_option`. Alembic stores option values in a `configparser` using `BasicInterpolation`, which treats a bare `%` as interpolation syntax; a percent-encoded Windows path (`C%3A%5CUsers%5C...`) otherwise raises `ValueError: invalid interpolation syntax` during startup. `get_main_option` decodes `%%` back to `%`, so the URL handed to SQLAlchemy is unchanged.

#### Scenario: Windows path does not crash migration inspection

- **GIVEN** the default SQLite URL on Windows, percent-encoded by `URL.render_as_string()` into `sqlite:///C%3A%5CUsers%5C...%5Cstore.db`
- **WHEN** the Alembic `Config` is built for migration inspection
- **THEN** no `ValueError: invalid interpolation syntax` is raised
- **AND** `get_main_option("sqlalchemy.url")` returns the original percent-encoded URL unchanged

#### Scenario: Round-trip preserves an already-encoded percent

- **GIVEN** a path that already contains a percent-encoded `%` (rendered as `%25`)
- **WHEN** the escape turns it into `%%25` and `get_main_option` decodes it
- **THEN** the value SQLAlchemy receives decodes back to `%25`, i.e. the original URL is preserved exactly

#### Scenario: Non-Windows URLs are unaffected

- **GIVEN** a SQLite or PostgreSQL URL whose path contains no `%`
- **WHEN** the escape and decode round-trip is applied
- **THEN** the URL is unchanged and migration behavior is identical to before
