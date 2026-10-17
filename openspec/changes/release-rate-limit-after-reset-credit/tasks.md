## Implementation

- [x] Thread the redemption start time (read before the consume request) through forced usage refresh, and bypass the rate-limited cooldown guard only when the block's second precedes it and the persisted deadline is bound to exactly one quota window whose fresh deadline moved.
- [x] Pass confirmation time from every reset-credit redemption path (accounts API, dashboard API, scheduler auto-redeem) only for `code == "reset"` with `windows_reset > 0`.
- [x] Add real-database integration coverage for each redemption path and for negative outcomes: unconfirmed redemption, exhausted usage, unbound or ambiguous deadline, unmoved blocked window, a 429 recorded during the redemption on both the dashboard and scheduler paths, and a usage refresh recorded during the redemption.
- [x] Remove the temporary weekly reset reproduction test after recording its findings in context.
- [x] Run the scoped integration tests, lint/format, timing-seam check, and strict OpenSpec validations.
