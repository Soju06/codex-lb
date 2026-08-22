## 1. Focus regressions

- [x] 1.1 Add focused telemetry-preview coverage for Escape and explicit Close returning focus to the exact invoker without activating `document.body`.
- [x] 1.2 Add focused password-setup coverage for Escape and Cancel returning focus to the exact invoker without submitting setup or activating `document.body`.
- [x] 1.3 Prove the new focus regressions fail on the dispatched baseline before implementing the fix.

## 2. Local focus restoration

- [x] 2.1 Register `View collected data` as the telemetry dialog's native trigger while preserving on-demand preview mounting.
- [x] 2.2 Register `Set password` through `PasswordSetupDialog` while retaining `activeDialog` as the controlled password-dialog state.
- [x] 2.3 Preserve telemetry fetching/mutation behavior, password auth/refresh/toast behavior, and both dialogs' conditional mounting semantics.

## 3. Verification

- [x] 3.1 Run the focused affected Vitest files and confirm all focus and existing side-effect regressions pass.
- [x] 3.2 Run affected frontend formatting/lint/typecheck subsets and scoped strict OpenSpec validation.
- [x] 3.3 Browser-verify both Settings flows for Escape and explicit dismissal, including exact active element and unchanged scroll position, and record honest before/after evidence for the PR.
