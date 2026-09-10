# Verification

Public regressions reproduced the original migration-head ambiguity, invalid empty WebSocket close status, retained database checkout during inventory HTTP, and wrong 503 response for a permanent binding conflict. The fixes passed 236 combined Desktop/usage checks and 110 migration controls on disposable SQLite, with PostgreSQL-only cases skipped locally. Historical hosted PostgreSQL migration and test results cover the first repair candidate, `2bc905f81`; they are not evidence for later commits.

A follow-up expired-token route regression found database connections retained across OAuth HTTP. A detached account and the existing per-operation background repository remove that retention. The route now passes both normal refresh and request-timeout cases; a retry joins the continuing refresh without exchanging the token twice. The reset control run passed 38 cases. The subsequent focused OAuth, migration, guard and CI-contract run passed 22 cases, with four PostgreSQL cases skipped locally. Counts overlap.

The migration fixture's old db_setup dependency reset the database before a guard could run. A public pytest probe reproduced that ordering without opening a database. The corrected fixture validates the explicitly configured codex_lb_test target before creating its engine, and the CI services and Makefile default use that disposable name. The fixture probes reject ordinary, misleading, missing and mismatched database targets.

The timeout suggestion is not a defect: the existing context specifies a ten-second aggregate deadline. The existing GET/POST timeout tests prove cancellation of active and queued refreshes and no consumption. That contract is preserved.

Ruff, full type and architecture checks, main specifications and repair deltas passed. Hosted proof for `fec5deb2901adea848d2915f0f0efdfffc8ab9ad` is recorded below. Readiness owns subsequent head/base changes and review monitoring. No real credit, live database, Docker service or routing configuration was changed. Hosted results do not establish live Desktop acceptance.

## Hosted proof for the repaired candidate

[CI run 34455760455](https://github.com/Soju06/codex-lb/actions/runs/34455760455) completed successfully with head SHA `fec5deb2901adea848d2915f0f0efdfffc8ab9ad`. Its [CI Required gate](https://github.com/Soju06/codex-lb/actions/runs/34455760455/job/102812223768), [PostgreSQL tests](https://github.com/Soju06/codex-lb/actions/runs/34455760455/job/102802769118) and [PostgreSQL migration check](https://github.com/Soju06/codex-lb/actions/runs/34455760455/job/102802769166) all passed. This completes the hosted-check task for that candidate; no unchanged CI rerun is needed.

[CodeRabbit review 5167558086](https://github.com/Soju06/codex-lb/pull/2289#pullrequestreview-5167558086) subsequently reviewed the same SHA and requested this evidence correction. These records bind proof to `fec5deb2901adea848d2915f0f0efdfffc8ab9ad`; they do not claim CI coverage of a later documentation commit or compatibility with a newer main. Mergeability and any later head's checks require separate verification.
