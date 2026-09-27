## Why

The full-width List redesign inadvertently replaced the existing account selection layout, where accounts are on the left and selected-account statistics, charts and management are on the right. The operator wants overview modes added while retaining that original layout.

## What Changes

- Restore the original split account/detail layout as an explicit Detail view and as the default without a stored preference.
- Retain full-width List and Grid as additional modes, preserving their filters, selection, pagination and detail dialogs.
- Preserve recorded subscription information in selected details and the overviews.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `frontend-architecture`: Restore the original selectable account/detail layout alongside overview modes.

## Impact

Accounts frontend, translations, regression tests, browser screenshots and context. No backend, schema, dependency or deployment changes.
