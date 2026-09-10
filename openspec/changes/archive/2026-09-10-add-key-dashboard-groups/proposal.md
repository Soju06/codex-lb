## Why

Key holders need to compare their team's recent usage without administrator access. The self-service dashboard currently exposes only the authenticated key's usage.

## What Changes

- Add an optional administrator-managed usage group to API keys. Identical group names share aggregate usage; existing keys remain ungrouped.
- Add a Group keys tab displaying current members and their requests, tokens, cached tokens, and cost over the preceding 30 days.
- Authenticate group reads with the current key and expose only display names, masked prefixes, and usage totals.

## Capabilities

### New Capabilities

### Modified Capabilities

- `api-key-dashboard`: Group membership administration and privacy-safe 30-day group statistics in a new self-service tab.

## Impact

API-key persistence and administrator create/edit forms, an additive Alembic migration, key-dashboard API and React UI, localized copy, integration tests, and the existing API-key documentation. No new settings, runtime dependencies, proxy permissions, or main navigation items.
