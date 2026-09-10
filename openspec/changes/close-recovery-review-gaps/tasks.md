## Implementation and verification

- [x] Preserve session-scoped legacy root fingerprints and verify completed-root admission and scope fences (1260 bridge tests pass).

- [x] Add regression cases for nested metadata ordering, proxy-injected terse anchor errors, and timestamp ties.
- [x] Correct normalization and response lookup ordering; verify existing retry classification is sufficient.
- [x] Make fence tests assert both permitted pre-fence schedules and reject late stale events.
- [x] Validate focused tests, quality gates, and strict OpenSpec validation.
- [x] Run the full unit suite with source files unchanged throughout execution: 9594 passed, 98 skipped.

Current-head cloud CI and reviewer verification are tracked on PR #1900 after
publishing these fixes; local verification does not substitute for those gates.
