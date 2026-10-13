# Verified evidence

This change does not alter how any dialog renders: layout, overlay and content
pixels are identical before and after. The evidence is therefore behavioural, not
screenshots.

## Browser regression (Chromium, 2026-10-13)

`make test-dashboard-browser-smoke` against the built dashboard and an isolated
real backend. The new test opens the Create API key dialog, appends a top-most
stand-in suggestion button to `<body>`, and clicks it with Playwright's
actionability checks.

Before (main `f8ffbac2`, fix reverted, rebuilt):

```
✘ an open dialog leaves password-manager suggestions drawn into the page clickable
  Error: locator.click: Test timeout of 30000ms exceeded.
    - <div data-state="open" aria-hidden="true" data-aria-hidden="true" data-slot="dialog-overlay" class="… fixed inset-0 z-50 bg-black/50"></div> intercepts pointer events
  1 failed, 8 passed
```

After (this change, rebuilt):

```
✓ an open dialog leaves password-manager suggestions drawn into the page clickable
  9 passed
```

## Unit regression

`frontend/src/components/ui/dialog.test.tsx`. Before, a press on a body-level
third-party element dismissed the dialog (`onOpenChange(false)`). After, the dialog
stays open, and an overlay press still closes it.

## Manual check with the real extension

macOS 27.0, Chrome 154.0.8037.57, iCloud Passwords extension 3.3.0, against a
locally built dashboard from this change:

- The extension's "Enable Password AutoFill" prompt over Settings → Enable TOTP is
  clickable.
- Choosing the Apple Passwords verification-code suggestion fills the six code
  slots and signs in.

On the released 1.24.0 dashboard, the same suggestions show but do nothing (#2418,
#2603).
