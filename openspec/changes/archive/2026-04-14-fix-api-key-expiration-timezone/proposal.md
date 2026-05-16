## Why
Creating or updating an API key with `expiresAt` can fail against PostgreSQL when clients send an ISO 8601 datetime with timezone information. The dashboard and API clients already send offset-aware values, but the backend persists `expires_at` into a `timestamp without time zone` column and should normalize those datetimes before writing.

## What Changes
- Normalize API key expiration datetimes to UTC naive before persistence.
- Preserve the public contract that `expiresAt` may be submitted with a timezone offset.
- Add regression coverage for API key create and update flows with timezone-aware expiration values.

## Impact
- Prevents PostgreSQL datetime binding failures on offset-aware `expiresAt` values.
- Does not change the database backend contract or the default SQLite runtime behavior.
