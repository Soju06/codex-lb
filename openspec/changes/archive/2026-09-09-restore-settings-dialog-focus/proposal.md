## Why

The Settings `View collected data` and `Set password` dialogs leave focus on the document body after they close, so keyboard users lose their place. Both controlled dialog flows need to restore focus to the exact button that opened them without changing their existing data or authentication behavior.

## What Changes

- Retain the `View collected data` and `Set password` invoker elements while their controlled dialogs are open.
- Restore focus to the exact invoker when either dialog closes through Escape or its explicit Close/Cancel action.
- Add focused regressions for both dismissal paths while preserving Settings scroll position, conditional mounting, telemetry requests, and password side effects.
- Keep shared dialog primitives and the change/remove/verify/TOTP dialog flows unchanged.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `frontend-architecture`: Settings dialog dismissal restores keyboard focus to the exact invoking control for the two affected flows.

## Impact

Dashboard SPA only: `telemetry-settings.tsx`, `password-settings.tsx`, `password-setup-dialog.tsx` only as required, their focused component tests, and the `frontend-architecture` delta spec. No API, database, shared dialog primitive, navigation, dependency, or product-side-effect changes.
