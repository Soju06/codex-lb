## Why

Local SQLite deployments can run with WAL enabled and multiple async database
connections. Pre-migration backups that copy only the main database file can miss
uncheckpointed WAL pages, and account writes that bypass the shared SQLite write
section can contend with other writer paths during high account churn.

## What Changes

- Create SQLite pre-migration backups through SQLite's online backup API so WAL
  content is included in the backup database.
- Route account mutation methods through the shared SQLite writer section while
  preserving PostgreSQL locking behavior.

## Impact

- **Database safety**: local SQLite backups are consistent snapshots, including
  WAL-resident rows.
- **Runtime behavior**: account import, reauth, token refresh, status updates,
  account preferences, and account deletion serialize with other SQLite write
  sections.
