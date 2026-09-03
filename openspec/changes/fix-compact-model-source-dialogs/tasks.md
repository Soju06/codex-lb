## 1. Regression coverage

- [x] 1.1 Add a browser regression proving the viewport-bounded shell, one shrinking internal scroll region, a persistently visible submit action, and that the end of the form content is scrollable into view.
- [x] 1.2 Enable the Reasoning capability inside the regression so the form reaches the height that triggered the bug; with the capability off the default form still fits a desktop viewport (measured `scrollHeight` 650 = `clientHeight` 650 at 1440x900).
- [x] 1.3 Run the regression against the baseline layout and confirm it fails before the implementation change.

## 2. Dialog layout

- [x] 2.1 Convert the model source create dialog to the viewport-bounded flex-column shell established by `fix-compact-api-key-dialog`, with the header and footer outside the scroller.
- [x] 2.2 Apply the same shell to the model source edit dialog, whose form and footer live in the inner form component, and pad the no-selection fallback for the now-unpadded shell.

## 3. Verification

- [x] 3.1 Run the frontend typecheck, lint, and model-sources component tests.
- [x] 3.2 Run the dashboard browser-smoke suite (6 passed).
- [x] 3.3 Validate the scoped OpenSpec change with strict validation.
- [x] 3.4 Capture before/after browser measurements at 320x568 and 1440x900.

## 4. Follow-up (out of scope)

- [ ] 4.1 The capability checkboxes are Radix `button[role="checkbox"]` elements with no
      `aria-label`, no `aria-labelledby`, and empty text content, so they expose no
      accessible name; only the wrapping `label` carries the text. Naming them is an
      accessibility fix independent of this viewport change and belongs in its own change.
