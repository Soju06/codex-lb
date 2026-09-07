## Why

The Install tab presents setup actions and warnings as a flat block, making the primary action hard to scan. Give setup a clearer visual hierarchy without changing installer behavior or the Overview tab.

## What Changes

- Present platform selection and the terminal command as numbered setup steps.
- Separate file export and script preview from the primary command.
- Group prerequisites, backup/restart guidance, and credential warnings into supporting cards.
- Support keyboard use, narrow screens, and light/dark themes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-key-dashboard`: Define a responsive, accessible Install presentation with distinct setup and export actions.

## Impact

Only Install UI, its translations, tests, and OpenSpec documentation. No installer, authentication, API, Overview, dependency, or deployment changes.
