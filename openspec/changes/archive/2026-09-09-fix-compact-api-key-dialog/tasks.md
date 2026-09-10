## 1. Regression coverage

- [x] 1.1 Add a focused browser regression that proves the viewport-bounded dialog shell, one shrinking internal scroll region, persistent controls, field reachability, and dismissal behavior.
- [x] 1.2 Run the focused regression against the baseline layout and record that it fails before the implementation change.

## 2. Dialog layout

- [x] 2.1 Convert the API key create dialog to the established viewport-bounded flex-column shell with persistent header and footer.
- [x] 2.2 Move the existing responsive form grid into one `min-h-0` internal scroller and remove the two independent column scroll constraints without changing fields or submission behavior.

## 3. Verification

- [x] 3.1 Run the focused component tests and affected frontend format, lint, and typecheck subsets.
- [x] 3.2 Validate the scoped OpenSpec change and verify implementation completeness, correctness, and coherence.
- [x] 3.3 Exercise the real `/apis` create dialog at 320x568, 390x844, and desktop, including internal scrolling plus overlay and Escape dismissal, and capture honest before/after browser evidence.
