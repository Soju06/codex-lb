## 1. Reproduce and retain publication

- [x] 1.1 Add direct-failure, cancellation/backoff, ambiguous-commit, and marker-race regressions; demonstrate the direct-path failures against unchanged main.
- [x] 1.2 Centralize marker consumption/restoration in the shared bump primitive; verify unit and bus integration tests pass, including non-starvation and successful local acknowledgement.

## 2. Product paths and scope

- [x] 2.1 Add real API-key disable and account-pause regressions with independent peer caches and failed immediate writes; verify local mutation success and post-recovery peer convergence.
- [x] 2.2 Keep namespace/callback registrations, route-cache/settings publications, schema, UI, and unrelated PR changes untouched; verify the base diff is confined to the retry fix, tests, and OpenSpec.
- [x] 2.3 Sync the resilience requirement and stable query-caching context, including process-loss limitations and an example; verify strict change and stable-spec validation.

## 3. Verification

- [x] 3.1 Run relevant cache/auth/account/settings suites, lint/type checks, migration topology, and simplicity budgets; record exact results and any limitations in verification.md.
- [x] 3.2 Review task/spec/implementation coherence and the final diff; record review evidence before publishing the independent PR.
