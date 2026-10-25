# Verification

## Completeness

The two runtime modules now own their detached tasks explicitly. Refresh completions are retained by the singleflight; callback idle-stop tasks are retained by the OAuth state store. Done callbacks release references. Callback failures are retrieved and produce a fixed diagnostic without exception text.

## Correctness and coherence

Existing refresh successor checks, cooldown rules, caller cancellation independence and callback stop serialization are unchanged. No account identifiers are added to task names. The implementation uses owner-local task sets rather than new scheduler or shutdown machinery. This is preventive robustness, not evidence of a production task disappearing under garbage collection.

The new lifecycle tests cover successful, failed and cancelled refresh outcomes, cancellation of completion before execution, and successful/failed/cancelled listener cleanup. They verify pending ownership and removal after completion, failure cooldown, listener cleanup and content-free errors. Existing full OAuth and auth-manager suites retain their route, race, token and callback assertions.

## Evidence

- Frozen final dependency environment: 110 tests passed across `test_owned_oauth_tasks.py`, `test_auth_manager.py` and `test_oauth_flow.py`.
- Ruff lint, formatting and scoped Ty passed.
- Targeted RUF006 check identifies three unretained spawns on unchanged main and passes on both modified modules. This is a static ownership regression, not a forced-GC runtime claim.
- OpenSpec 1.11.0 strict validation passed the change and all 67 canonical specifications after synchronization.
- Independent review found no introduced issue; its separate 12-test run and reset/clear probes preserved existing successor and callback behavior. A follow-up review verified the lock-boundary test improvement.
- The full local gate ran: frontend tests (1,658), lint/type checks, Rust tests/build and dependency checks passed. Python unit/simulation coverage reached 10,487 passed, 99 skipped and one expected failure, with one failure in `test_output_under_a_temporary_filesystem_is_refused`. The same failure was previously reproduced on unchanged main on this host. Later local gate stages were not reached; the full local gate is not claimed green. GitHub CI remains the integration gate.

No public authentication contract, settings, schema, dashboard, routing or credential handling changes. Existing dependency updates are reused from their separate maintenance commits solely to pass current dependency gates.
