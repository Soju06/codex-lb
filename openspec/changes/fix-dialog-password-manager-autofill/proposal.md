## Why

Every dashboard dialog is a modal Radix `Dialog`. While a dialog is open, Radix
sets `pointer-events: none` inline on `<body>`. It turns pointer events back on for
its own overlay and content, so the lock only reaches what the page doesn't own.
Some password managers draw their suggestions into the page. In Chrome on macOS,
the iCloud Passwords extension appends its suggestion list to the document next to
the focused field. That list inherits the lock: it shows over the dialog, but a
click passes through it to the overlay underneath.

The result is that Apple Passwords can't fill anything inside a dialog in Chrome.
This covers the sign-in authenticator code, the step-up confirmation (password
and code), TOTP setup and disable, and the password setup, change, verify and
remove dialogs. Even the extension's own "Enable Password AutoFill" prompt can't be
clicked. Safari draws the same suggestions as browser UI, so it was never affected.
Pasting a code always worked.

The `input-otp` field already advertises `autocomplete="one-time-code"` and
`inputmode="numeric"`, so field metadata is not what's missing.

## What Changes

- While a dashboard dialog is open, keep `<body>` pointer events enabled (one
  stylesheet rule keyed on the open dialog overlay). The full-screen overlay stays
  the click barrier for the page behind the dialog.
- In the shared `DialogContent`, a press outside the content dismisses the dialog
  only when it lands on the overlay. A press on anything else outside the content
  is a surface a third party drew over the dialog, such as a suggestion list, and
  must not close the dialog.
- Keep focus trapping, `aria-hidden` on the rest of the document, scroll locking,
  Escape and overlay dismissal unchanged.
- Add regression coverage: a browser test in Chromium that clicks a body-level
  stand-in suggestion over an open dialog, and a unit test for the dismissal rule.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: require open dashboard dialogs to leave page-level
  third-party surfaces, such as password-manager suggestions, clickable without
  dismissing the dialog.

## Impact

- `frontend/src/index.css` and `frontend/src/components/ui/dialog.tsx`. Every
  dialog built on the shared `Dialog` primitive is covered, with no call-site
  changes.
- Tests: `frontend/src/components/ui/dialog.test.tsx` (new) and
  `frontend/browser-smoke/dashboard.spec.ts`.
- No API, schema, persistence, dependency, configuration or i18n changes.
- `AlertDialog` and `Sheet` keep Radix's lock. None of them hosts a credential
  field today.
