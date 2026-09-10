## Verification and repair

- [x] Reconcile upstream main and verify one migration head with preserved data.
- [x] Reproduce and fix inventory connection retention and binding conflict responses through the existing reset route.
- [x] Verify relay close handling, lifecycle cleanup and independent quota expectations.
- [x] Verify hosted checks for `fec5deb2901adea848d2915f0f0efdfffc8ab9ad`: [CI run 34455760455](https://github.com/Soju06/codex-lb/actions/runs/34455760455) completed successfully. See `verification.md` for the exact job evidence.

- [x] Verify expired-token inventory refresh releases database connections across OAuth HTTP.
- [x] Guard destructive PostgreSQL test setup before any database reset.
