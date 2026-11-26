# Evidence and scope

Issue #1367 reported Team quota on 1.21.0; its later payload used 43800 minutes, while exact 43200 minutes was hidden by capacity gating. The maintainer confirmed both on September 8 and approved a 28–32-day band with no frontend change. Fresh upstream main 0f6a31c56ac30804ca1c0fac27ca02c6f59bf2b0, package 1.25.0b6, reproduced the exact-duration case through GET /api/accounts: 24 percent used produced 76 percent monthly remaining for Free and null for Team.

Poll normalization failed seven of eight accepted duration/placeholder cases before its correction. Live ingestion through GET /api/accounts failed three of four 43200/43800 plus absent/zero-secondary cases before its correction. Those cases pass after the fix. Both directions of the monthly/short-window transition are covered. No account credentials or live runtime data were used.

A Team account with a lone 43800-minute window and 96 percent used now exposes Monthly 4 percent; monthly credit capacity and remaining credits stay unknown. Existing rows are preserved and normal refresh supplies a newly normalized monthly row. This is a presentation and ingestion correction, not new paid-plan capacity estimates or a routing change.

Verification: affected suites passed 252 tests with four skips; the additional reverse-transition cases passed seven selected tests. Ruff and ty passed. All 65 main specs and this delta passed strict OpenSpec 1.11.0 validation. An independent read-only review found no actionable correctness issue against the pinned base and seven-file source/test diff.
