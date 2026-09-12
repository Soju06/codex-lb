# Local PostgreSQL migration and dual backends

## Why
SQLite's lifetime lock prevents overlapping local backends. Move the local deployment to PostgreSQL before enabling validated dual-instance switching.

## What Changes
- Add a conservative SQLite snapshot importer into an empty, matching PostgreSQL schema with atomic copy and full row verification.
- Validate two instances sharing PostgreSQL and preserve the old SQLite deployment and client routing until the new deployment is accepted.
- Extend the private entrance database plan to permit PostgreSQL candidates only with matching release schemas and revision.

## Impact
Local operator scripts and deployment documentation; no application schema changes.
