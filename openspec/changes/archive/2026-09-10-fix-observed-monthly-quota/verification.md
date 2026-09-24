# Verification

Base: upstream/main `0f6a31c56ac30804ca1c0fac27ca02c6f59bf2b0`.

All three tasks are complete. The added observed-monthly requirement is implemented by shared duration classification, poll/live normalization and account-summary preservation. The renamed presentation requirement retains its original behavior and now names its plan-independent scope.

- Team observation and zero secondary scenarios: live ingestion through GET /api/accounts, 43200 and 43800 minutes, verifies Monthly 4 percent, preserved duration, absent short/weekly slots and null credit estimate.
- Duration boundaries and ordinary/unknown secondary scenarios: public normalization tests cover inclusive 40320/46080 and excluded 40319/46081, 300, 10080 and unknown durations.
- Paid upgrade scenario: the existing GET /api/accounts regression proves newer primary or secondary usage supersedes older monthly usage.
- Reverse transition: additional GET /api/accounts cases prove newer monthly history supersedes older primary or secondary history.
- Polling: the existing refresh test now covers Team 43800 alongside Free 43200.

Affected suite: 252 passed, four skipped. Follow-up reverse-transition selection: seven passed. Ruff, ty, diff whitespace checks and 65 main specs passed. Strict change validation passed before archive; strict main validation passed after sync. Tests use one dedicated disposable SQLite database for main, background and fixture engines. No live acceptance is claimed.

Independent read-only review found no actionable correctness issue. Its suggested reverse-transition proof and wording correction were applied. Screenshot proof uses the unchanged AccountCard and synthetic before/after quota payloads; it is component rendering evidence, not production capture. The two browser assertions passed. No product frontend source was changed.
