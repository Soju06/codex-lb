## 1. Repair composition

- [x] 1.1 Reproduce the new two-head public CLI failure against the pinned incoming main.
- [x] 1.2 Preserve historical migration tests and add populated-parent/default/merge-only downgrade proof for the new join.
- [x] 1.3 Resolve the test conflict preserving both intents and add the no-op migration without rewriting history.

## 2. Verify candidate

- [x] 2.1 Verify public upgrade/check, affected migration suites and guest-session/model compatibility.
- [x] 2.2 Run required static checks and strict specs validation.
- [x] 2.3 Obtain independent Medium review of this exact composition and repair, then sync and archive the verified change.

Local verification covers 58 migration cases (57 initial passes plus the corrected expected-head test), 50 guest/session/model compatibility cases and 3 receipt-purge controls. Eight PostgreSQL cases were skipped locally. Full static checks, 65 strict main specs and independent Medium review passed for tree1295efb1. Hosted verification remains a delivery gate after publication.
