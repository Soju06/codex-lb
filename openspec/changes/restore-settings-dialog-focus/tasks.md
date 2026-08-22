## 1. Focus regressions

- [x] 1.1 Add focused telemetry-preview coverage for Escape and explicit Close returning focus to the exact invoker without activating `document.body` or changing scroll position.
- [x] 1.2 Add focused password-setup coverage for Escape and Cancel returning focus to the exact invoker without submitting setup, activating `document.body`, or changing scroll position.
- [x] 1.3 Prove the new focus regressions fail on the dispatched baseline before implementing the fix.

## 2. Local focus restoration

- [x] 2.1 Retain the `View collected data` invoker and restore it from the telemetry dialog close-auto-focus lifecycle with scroll prevention.
- [x] 2.2 Retain the `Set password` invoker in `PasswordSettings` and restore it from `PasswordSetupDialog` only while that exact element remains connected.
- [x] 2.3 Preserve telemetry fetching/mutation behavior, password auth/refresh/toast behavior, and both dialogs' conditional mounting semantics.

## 3. Verification

- [x] 3.1 Run the focused affected Vitest files and confirm all focus and existing side-effect regressions pass.
- [x] 3.2 Run affected frontend formatting/lint/typecheck subsets and scoped strict OpenSpec validation.
- [x] 3.3 Browser-verify both Settings flows for Escape and explicit dismissal, including exact active element and unchanged scroll position, and record honest before/after evidence for the PR.
