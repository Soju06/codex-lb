## Why

Request logs currently classify `Codex Desktop/...` as `Codex` because grouping stops at whitespace before it considers the product-version delimiter. This loses the actual client family in reports and existing request-log rows.

## What Changes

- Derive request-log `useragent_group` from all characters before the first `/` in the inbound `User-Agent` value.
- Preserve the existing handling for values without `/`.
- Add an Alembic data migration that backfills only non-null `useragent` rows containing `/`, without trimming or other whitespace preprocessing.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: Request-log user-agent grouping uses the complete pre-slash client family rather than the first whitespace-delimited token.

## Impact

- `app/modules/proxy/_service/support.py` request-log user-agent extraction
- `request_logs.useragent_group` historical data through a new Alembic revision
- Proxy user-agent extraction tests and migration validation
