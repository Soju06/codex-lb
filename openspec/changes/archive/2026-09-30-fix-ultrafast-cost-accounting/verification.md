# Verification

Verified against upstream main `f8ffbac2099a113fba54dfd8d77774f5bca80ffa`.

- Baseline: six calculator regressions fail; Responses regression has ten
  confirmed-Ultrafast failures and two integral-microdollar settlement failures.
- Focused pytest: 491 passing tests across pricing, catalogs, metadata lifecycle,
  API-key routes/service, logs, usage updater and external source adapters.
- Real HTTP: eight streaming/non-streaming Responses cases with a local
  synthetic upstream. Confirmed Ultrafast costs USD9.00; actual-default downgrade
  USD1.50; 272000 and 272001 input boundaries with cached reads USD18.24 and
  USD34.98012. Request logs, displayed breakdowns and settled microdollars agree.
- Every HTTP case denies the next over-limit request with 429 without a second
  upstream dispatch and deletes its synthetic key with 204. App/upstream tasks,
  temporary database and runtime directories are removed.
- Changed Python diagnostics, Ruff check/format, `ty check app`, wheel build,
  strict change validation and both affected canonical specs pass.
- Full `uv run pre-commit run local-ci --hook-stage manual --all-files` was run:
  frontend 1649 tests, frontend build/lint/types, architecture checks, Python
  lint/types and Rust tests passed. It stopped at `cargo deny` not installed
  (exit 101), before the complete backend suite. This is not a green full gate.
- Global strict OpenSpec validation has 17 failures on both this worktree and
  unchanged upstream; the two affected canonical specs pass.

No historical non-NULL cost rewrite, subscription quota multiplier, schema,
frontend or dependency change. Cache-write accounting remains separate in
PR #2504. Metadata refresh PR #2544 adds model/version metadata, not this fix.
