## Why

PR #2118's review identified two gaps in the documented Japanese dashboard behavior: Reports generation timestamps use the browser locale, and API-key edit usage labels still contain English. Both surfaces already have shared formatting or translation resources available.

## What Changes

- Apply the selected locale and date/time preferences to Reports generation timestamps.
- Translate API-key edit usage types, windows, and the all-model label.
- Add regression coverage and before/after captures for the affected screens.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Explicitly cover Reports generation timestamps and API-key edit usage labels in the existing localization requirements.

## Impact

Two frontend components, their existing tests, browser capture coverage, and OpenSpec documentation. No API, schema, dependency, or configuration changes.
