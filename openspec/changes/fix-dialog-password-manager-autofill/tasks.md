## 1. Regression coverage

- [x] 1.1 Add a Chromium browser-smoke test that opens a dashboard dialog, appends a stand-in suggestion element to `<body>` above the overlay, clicks it with Playwright's actionability checks, asserts it received the click and the dialog stayed open, then confirms an overlay press still closes the dialog.
- [x] 1.2 Add a `Dialog` unit test proving a press on a body-level third-party element does not dismiss the dialog, while a press on the overlay still does.
- [x] 1.3 Run both against the baseline and confirm they fail before the change (browser: "dialog-overlay intercepts pointer events"; unit: the dialog dismissed).

## 2. Shared dialog primitive

- [x] 2.1 Keep `<body>` pointer events enabled while a dialog overlay is open (`frontend/src/index.css`).
- [x] 2.2 Restrict outside-press dismissal in `DialogContent` to presses on the overlay.

## 3. Verification

- [x] 3.1 Run the frontend typecheck, lint, the full vitest suite, and the dashboard browser-smoke suite.
- [x] 3.2 Validate the change with strict OpenSpec validation.
- [x] 3.3 Confirm in Chrome on macOS with the iCloud Passwords extension that its "Enable Password AutoFill" prompt is clickable over the TOTP setup dialog and that the verification-code suggestion fills the sign-in TOTP code.
- [x] 3.4 Record before/after browser and unit regression output and the manual extension check (see evidence/README.md); no screenshots, as rendering is unchanged.
