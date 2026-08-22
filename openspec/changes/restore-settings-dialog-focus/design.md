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

1. **Retain each invoker with a local button ref.** `TelemetrySettings` owns the telemetry button ref. `PasswordSettings` owns the setup button ref and passes it only to `PasswordSetupDialog`, because the parent owns the conditional action button. This records the exact element rather than looking it up later by label or selector.

2. **Restore during Radix's close-auto-focus lifecycle.** Each affected `DialogContent` uses `onCloseAutoFocus`. When the retained element is still connected, the handler prevents the fallback and calls `focus({ preventScroll: true })`. Restoring in this lifecycle avoids a timer or effect racing the focus scope teardown, and `preventScroll` preserves the operator's Settings position.

3. **Fall back when the invoker no longer exists.** The handler only prevents Radix's default when the retained element is connected. If successful password setup changes auth state and unmounts the setup action, the existing close behavior remains instead of focusing a detached node. Mutation, refresh, toast, and close ordering stay unchanged.

4. **Keep the fix local instead of changing the shared primitive.** Many dialogs already use real triggers or have different lifecycle needs. A shared default would broaden behavior beyond the two reproduced failures; duplicating the small close handler at these two ownership points is the narrower change.

## Risks / Trade-offs

- A future refactor that replaces either invoker must keep the ref attached; focused component tests guard the public focus behavior.
- The no-scroll guarantee depends on browser support for `HTMLElement.focus({ preventScroll: true })`, which is supported by the dashboard's modern-browser target.
- Successful password setup may remove the invoking button as auth state refreshes; in that case there is no connected exact target, so the guarded handler deliberately leaves the existing fallback behavior unchanged.
