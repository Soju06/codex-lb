# Reject Empty Migration Database URL

## Summary

Make the migration CLI distinguish an omitted database target from an explicitly supplied empty target so operator scripts cannot silently act on the settings-derived database.

## Why

`python -m app.db.migrate --db-url "" …` currently treats the empty argument as if `--db-url` were omitted. Every supported subcommand then resolves the settings-derived database; commands such as `upgrade` and `stamp` can mutate that unintended target.

## What Changes

- Reject an explicitly supplied exact empty `--db-url` as an argument error before settings resolution or database access.
- Preserve the settings-derived database fallback only when `--db-url` is omitted.
- Apply the rejection to all six migration subcommands and add real subprocess regression coverage for their database side effects.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `database-migrations`: Defines omission-versus-explicit-empty target handling for every migration CLI subcommand.

## Impact

- Affected code: `app/db/migrate.py`
- Affected tests: migration CLI subprocess integration coverage
- No Alembic revision or deployment configuration change is required.

## Non-Goals

- Whitespace-only URL handling, URL normalization, or URL syntax validation.
- Validation of an empty global database setting.
- Changes to Helm, Makefile, settings models, migration mechanics, or dashboard behavior.
