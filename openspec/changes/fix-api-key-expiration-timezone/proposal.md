# Proposal

## Why
Creating or updating an API key with `expiresAt` currently fails against PostgreSQL when the payload uses an ISO 8601 datetime with timezone information. The dashboard sends timezone-aware values (for example `2026-03-20T23:59:59.000Z`), but the backend persists `expires_at` into a `timestamp without time zone` column without normalizing it first, causing asyncpg to reject the write.

## What Changes
- Normalize API key expiration datetimes to UTC naive before persistence.
- Preserve the public contract that dashboard and API clients may submit ISO 8601 datetimes with timezone offsets for `expiresAt`.
- Add regression coverage for create and update flows with timezone-aware expiration values.
