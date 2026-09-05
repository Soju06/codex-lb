## 1. Account Health Recovery

- [x] 1.1 Preserve `usage_limit_reached` rate-limit classification and upstream deadlines.
- [x] 1.2 Require available applicable windows before early rate-limit recovery.
- [x] 1.3 Preserve explicit quota state after its debounce while refreshed long-window usage remains exhausted.

## 2. Regression Coverage

- [x] 2.1 Restore classifier and WebSocket assertions to the existing rate-limit contract.
- [x] 2.2 Add state-builder and database-backed selection regressions for fresh-but-exhausted long-window usage.
- [x] 2.3 Run focused proxy tests, lint, type checks, and strict OpenSpec validation.

## 3. Review Follow-up

- [x] 3.1 Prevent expired fallback deadlines from recovering exhausted resetless samples.
- [x] 3.2 Require post-block credit evidence before overriding explicit quota exhaustion.
- [x] 3.3 Verify repeated foreground selection and recovery across replicas.
- [x] 3.4 Reproduce primary/long-window exhaustion through the shared handler, persistence, and both replicas; prove valid recovery still works.
- [x] 3.5 Validate the revised PR with focused regressions, static checks, and strict OpenSpec validation.
