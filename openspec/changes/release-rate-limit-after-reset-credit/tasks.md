## Implementation

- [x] Thread optional reset-confirmation time through forced usage refresh and bypass only the rate-limited cooldown guard when confirmation postdates `blocked_at`.
- [x] Pass confirmation time from both reset-credit redemption paths only for `code == "reset"` with `windows_reset > 0`.
- [x] Add real-database integration coverage for both redemption paths and negative redemption/usage outcomes.
- [x] Remove the temporary weekly reset reproduction test after recording its findings in context.
- [x] Run the scoped integration tests, lint/format, timing-seam check, and strict OpenSpec validations.
