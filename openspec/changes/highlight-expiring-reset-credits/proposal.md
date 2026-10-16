## Why

Port the fork warning to the existing upstream account list item. Use the existing nearest-expiry summary field and one timer for the list, with cleanup on unmount. For example, two credits expiring tomorrow gain a warning; unknown expiry does not. This is display-only and does not change redemption policy or the existing Reset action countdown.

## What Changes

- Accounts list reset-credit expiry warning.
- Include regression coverage and before/after dashboard evidence.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: accounts list reset-credit expiry warning.

## Impact

Focused dashboard changes; the model-picker change also extends dashboard model metadata. No migrations, new settings, dependencies, navigation items, or setup steps.
