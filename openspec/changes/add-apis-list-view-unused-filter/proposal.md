# Proposal

## Why

Operators need to scan many API keys in compact rows and find keys that have not been used. The current sidebar and detail view emphasizes one key at a time.

## What Changes

- Add a remembered full-width List view alongside the existing default Detail view.
- Present key identity, status, lifetime requests, expiry, last use and pooled/API limit usage in compact responsive rows with sorting and pagination.
- Add All usage, Key not used and Used filters that combine with search and status in both views.
- Open the existing management/details panel in a dialog from List rows.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: APIs views, unused filtering and compact key rows.

## Impact

APIs frontend components, translations, component/browser tests and specs. Reuses existing key list and detail APIs without backend changes.
