# Verification — per-key Astra notes

Verified against upstream `3d23d53f89dbbaa2353040a30451cf90ca48ee92` with the new single migration head `20260923_000000_add_api_key_astra_notes`.

## Behavior and evidence

- Create/update/read round trips cover default off, explicit opt-in/out, and omitted/null patch preservation. Existing key-update cache invalidation applies to this preference.
- Both native catalog routes cover two-key isolation, updates, unchanged shared registry metadata, preserved upstream prompts, and unchanged generic model listings. Restriction cases cover absent/hidden/disallowed Astra, key visibility, another enforced model, source-provider Astra and incomplete metadata.
- Strict token-budget validation preserves unknown upstream fields and leaves malformed/incomplete metadata untouched. No upstream prompt is synthesized.
- SQLite and PostgreSQL verify historical keys default off, existing rows survive, and downgrade/upgrade restores the default. Fresh PostgreSQL schema and drift checks pass. Historical upgrade coverage retains context ownership, participant rows and dashboard credentials.
- Dashboard create/edit tests cover the off default, saved state and both toggle directions. Before/after screenshots were captured from the rendered dialogs and visually inspected.

## Real-client validation

An isolated loopback proxy used this checkout's application code, a copied database and two disposable API keys. The active proxy and client configuration were not changed.

| Client | Astra | Sol | Luna |
| --- | --- | --- | --- |
| Desktop engine `0.155.0-alpha.9.2` | Actual `notes.write_file` and `notes.read_file`, expected answer | Successful new task | Successful new task |
| CLI `0.156.0` | Actual `notes.write_file` and `notes.read_file`, expected answer | Successful new task | Successful new task |

These runs used remote discovery without `model_catalog_json` or a global token-budget override. Astra rollouts contain the actual notes calls; Sol/Luna rollouts contain no notes calls. Backend logs recorded six successful context requests and ten successful normal requests, with no errors. Context results are encrypted; verification is based on recorded tool calls, successful backend requests and the model's expected answer, not plaintext extraction of the returned note.

The tested Desktop engine uses registered Codex authentication for discovery and rejects the newer `model_catalog_url` setting. CLI uses that provider setting. Both kept a valid ChatGPT login for experimental tool eligibility; API-key-only discovery is not proof of notes eligibility. The evaluation containers were stopped and temporary authentication removed afterward.

## Automated checks

- Focused backend catalog, API-key and migration checks passed on SQLite and PostgreSQL.
- Frontend: 181 test files, 1,645 tests passed; lint, typecheck and build passed. Coverage: 84.08% lines, 77.52% branches, 79.86% functions.
- Python lint/format, full Ty, migration topology and simplicity budgets passed. Fresh SQLite migration reached the new head with no schema drift.
- Rust formatting, Clippy, 28 tests and release build passed.
- OpenSpec change and all 67 specifications passed strict validation.

- Unit/simulation/request-log suite: 10,418 passed, 98 skipped, one expected failure in the initial run. Its three failures were the committed-graph check and two synthetic API-key rows described below. A focused rerun of both affected files passed all 38 tests after correction.
- Historical context upgrades: all 11 starting revisions passed after updating the expected head.

- Integration-core shards 1 and 2: 1,057 passed / 329 skipped and 996 passed / 34 skipped. Shard 1 initially reported the 11 stale migration-head assertions; their corrected rerun passed all 11 cases.
- HTTP/WebSocket bridge plus end-to-end suite: 383 passed, one opt-in installed-Codex proof skipped. The separate real-client proof above exercised the new feature.

- Integration-core shard 3: 981 passed, 34 skipped. The deterministic three-shard partition was verified before execution.
- Package build and wheel asset verification passed using the already-verified frontend and a separate production-dependency environment.

Scopes overlap; totals must not be added. Full suites were not repeated after the focused test-fixture corrections; every initially failing case was rerun successfully.

## Test maintenance

The new column required updating three synthetic API-key rows. The historical context migration test now targets the current head while retaining its original preservation assertions. A topology test reads the committed Git graph, so its focused rerun was performed after the migration commit.

Four migration failures also reproduced on the pre-change commit `ace63a6`: rejection of unowned context tables now tests the context revision itself rather than whichever independent branch Alembic visits first; the retired `model_source_pins` index check now tests the revision that introduced that index, before the later upstream table removal. Production code was not changed for these failures.

## Remaining gates and limits

`make ci` stopped at `rust-audit` because `cargo-deny` is not installed. The remaining relevant Python suites are run separately. Docker vulnerability scanning and Helm/Kubernetes CI checks are not claimed as passed locally. Current-head GitHub CI and maintainer review remain independent merge gates.

The preference changes catalog defaults for new tasks after the client refreshes its catalog. It does not reconfigure an already running task, revoke context access or delete stored notes. No production deployment or active-client migration is part of this change.
