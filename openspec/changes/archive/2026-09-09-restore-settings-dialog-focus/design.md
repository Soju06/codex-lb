## Context

The telemetry preview and password setup flows use controlled Radix dialogs without a `DialogTrigger`. When either dialog unmounts after Escape or its explicit dismissal action, the dialog cannot identify the invoker and focus falls back to `document.body`. The `View collected data` and `Set password` buttons remain the correct return targets for the proven dismissal flows.

The change is limited to those two Settings paths. Telemetry preview fetching remains conditional on `previewOpen`; password setup keeps its existing mutation, session refresh, toast, form reset, and conditional action rendering.

## Goals / Non-Goals

**Goals:**

- Return focus to the exact `View collected data` or `Set password` button that opened the dialog.
- Cover both Escape and the explicit Close/Cancel action.
- Keep `document.body` from becoming the active element after those closes.
- Restore focus without changing the Settings page scroll position.
- Preserve telemetry and password product side effects and dialog mounting behavior.

**Non-Goals:**

- Shared `dialog.tsx` or `useFloatingLayerDismissGuard` changes.
- Password change, remove, verify, or TOTP dialog behavior.
- New focus-management abstractions or visual changes.
- API, authentication, telemetry payload, or persistence changes.

## Decisions

1. **Use each dialog's real invoker as a Radix `DialogTrigger`.** Both buttons render through `DialogTrigger asChild`, so the dialog root registers the exact DOM element that opened it and Radix restores that element through its established close-auto-focus lifecycle. This matches the working trigger-backed Language menu instead of adding a second focus mechanism.

2. **Keep controlled state and conditional content.** The telemetry trigger and conditionally rendered content share the existing `previewOpen` root, so the expensive preview query still starts only after opening. `PasswordSetupDialog` accepts its button as the trigger child at the existing action position; `activeDialog` remains the single controlled state, and dialog content/form cleanup still follows the existing open/close lifecycle.

3. **Do not duplicate focus handlers or broaden the shared primitive.** Native trigger registration removes the need for repeated `onCloseAutoFocus` callbacks, timers, DOM queries, or changes to `dialog.tsx` and `useFloatingLayerDismissGuard`. Other controlled dialogs retain their existing behavior.

4. **Split proof by environment.** Focus identity and dismissal behavior are permanent component regressions. Scroll preservation is exercised in real Chromium on a nonzero Settings scroll position because jsdom has no layout-driven focus scrolling.

## Risks / Trade-offs

- The trigger must remain mounted through Escape or explicit dismissal; both affected buttons do. Successful password setup can replace the setup action after session refresh, but that disappearing-trigger path is not one of the proven dismissal flows and keeps its existing auth lifecycle.
- Radix focuses the registered trigger after teardown. Real-browser proof covers both dismissal methods at nonzero scroll positions so a future layout or focus change cannot be mistaken for jsdom behavior.
