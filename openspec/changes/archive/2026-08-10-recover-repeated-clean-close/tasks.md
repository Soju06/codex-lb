- [x] Add bounded clean-close replay settings with safe defaults.
- [x] Allow one additional clean-close replay only before visible output.
- [x] Add jitter and dedicated retry diagnostics.
- [x] Add regression coverage for the second replay and retry cap.
- [x] Restart the upstream reader when pre-response recovery is initiated by the downstream stream task.
- [x] Add regression coverage for old-reader cancellation and replacement-reader ownership.
- [x] Keep the shared session live across the cancelled reader's socket-generation finalizer.
- [x] Add regression coverage for concurrent pruning during reader handoff.
- [x] Move the default pre-response recovery threshold ahead of the client timeout boundary.
- [x] Bound anchored stuck-gate grace and evaluate staleness from upstream activity/response creation.
- [x] Emit stuck-watchdog skip diagnostics with pending-state verdict inputs.
- [x] Add a forward-only repair for databases stamped before request-usage rollups were connected to the merge head.
- [x] Validate the OpenSpec change and run the focused and full test suites.
- [x] Build and deploy the validated image, then verify production health and logs.

## Post-deploy regression: idle retirement accounting

- [x] Require an owned eventless pending request before retirement advances the retry circuit.
- [x] Add lifecycle coverage proving idle no-pending retirement is neutral and eventless pending retirement records exactly one strike.
- [x] Add routed coverage proving an idle close plus one real timeout does not open the repeated-failure cooldown.
- [x] Run focused bridge suites, lint/type/architecture checks, and strict OpenSpec validation.
- [x] Build and deploy the revised image, then verify health and retry-circuit diagnostics.
