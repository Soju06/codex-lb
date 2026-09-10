# Fork compatibility and current-main verification, September 10

Integrated upstream `c858dcc864d59871f4dcd844db7286d1ef0d174c` with the existing PR. The runtime implementation is in `77b81dd4c91e21af8ad3a55ce683ea25fa8d3e35`. Results below supersede the September 8 migration-head and global OpenSpec statements. These are scoped local checks; current-head GitHub CI and the maintainer's architecture decision remain pending.

- Context codec/replay, history/notes, HTTP fork behavior, pool dispatch, cost/ownership, transient retries and realtime routes: 194 passed.
- Dedicated PostgreSQL 16 context, fork, dispatch-cost and ownership migration coverage: 51 passed.
- Fresh-upstream/deployed-context upgrade paths and populated overflow/transport branch round trips: 6 passed. The retained independent context head is accounted for during branch-specific downgrades.
- Dashboard schema tests: 43 passed. The complete candidate frontend build succeeded.
- `make lint`, full `ty check` and strict validation of the active change pass. Global strict OpenSpec validation: 66 passed, 0 failed.
- SQLite and PostgreSQL `codex-lb-db upgrade head` followed by `codex-lb-db check`: migration policy OK, no schema drift. A PostgreSQL database first upgraded to the originally deployed context revision retained its seeded owner and participant after reaching the merged head.

The context migration preserves its original parent `20260830_000000_add_quota_warmup_claim_expiry`. Its merge with upstream produces the sole head `20260910_120000_merge_codex_context_heads`.

An isolated candidate upgraded a consistent 1.34 GB copy of the deployed SQLite database. All original columns and rows in the checked tables matched their pre-upgrade hashes: 5 accounts, 7 API keys, 41,484 request logs, 216 context sessions and 222 participants. Integrity and foreign-key checks passed. The live volume was not upgraded.

Codex CLI/app-server 0.153.4 with Astra completed a real `thread/fork` through the candidate. The first fork request contained two authenticated results whose source UUID was the original task and whose target UUID was the fork. The recorded request returns 403 under the old codec and unfolds successfully under the candidate. The client wrote and read distinct notes for the parent and fork, then recovered both notes and queried both histories from a fresh client process after restarting the isolated proxy. The active client's authentication file remained byte-identical.

For deployment testing only, the image combined this context branch with the separate native WebSocket rejection patch `cf7d6f35`. Its 62 transport tests passed on current main. That patch is absent from the context PR diff. All 652 Python application files in the image match the reviewed context source, except the one explicitly patched transport file. This does not claim a real upstream outage was forced or that cross-replica recovery was exercised.

# Earlier verification, September 8

Base: `d3f63331d6ba5e233001fb38c9a059e5a9b681cb`, fetched from upstream main on 2026-09-08. These are scoped local results, not a claim that the full current-head GitHub matrix is green.

## Context and transport

- `pytest tests/integration/test_proxy_transient_retry.py tests/integration/test_codex_context_pool.py tests/integration/test_codex_history_notes.py tests/integration/test_codex_context_dispatch_cost.py tests/unit/test_codex_context_replay.py tests/unit/test_codex_upstream_paths.py tests/simulation/test_proxy_turn_lifecycle_property.py --timeout=60`: 205 passed, 1 skipped, 2 expected failures inherited from the simulation suite. The skipped callback-residue oracle requires CPython 3.14; this local environment uses 3.13.
- After preserving identity on HTTP bridge prewarm and strengthening the native WebSocket parsed-frame assertion, `pytest tests/integration/test_codex_context_dispatch_cost.py tests/integration/test_codex_context_pool.py --timeout=60`: 37 passed.
- The new cases observe actual context SQL on the HTTP route, confirm zero database work for ordinary and repeated dispatches, one participant insert on rotation, durable rejection of a conflicting marked request after cache eviction/clear, no cache publication after a failed commit, and cleanup of all history tasks on an injected deadline or cancellation. The prewarm case asserts the durable key fence before the first send.

## Migrations and PostgreSQL

- `pytest tests/integration/test_migrations.py tests/unit/test_db_migrate.py --timeout=90`: 97 passed, 7 PostgreSQL-only skips on SQLite.
- Against a dedicated temporary PostgreSQL 16 database, `pytest tests/integration/test_codex_context_pool.py tests/integration/test_codex_context_dispatch_cost.py tests/integration/test_migrations.py --timeout=90`: 82 passed.
- The final prewarm case and the four native WebSocket/HTTP bridge quota replay cases passed on PostgreSQL after the final transport adjustment: 5 passed.
- Alembic has a single head, `20260905_120000_add_codex_context_ownership`, parented on current main's `20260908_020000_merge_overflow_transport_heads`. That merge revision includes the subscription-overflow migration from #2165. Schema drift, upgrade/downgrade and rejected-adoption preservation pass on both dialects.

The suites overlap and include reruns; the numbers must not be summed as distinct tests.

## Static checks and specifications

Ruff, formatting and targeted Ty checks pass. `check_proxy_timing_seams.py`, `check_proxy_architecture.py`, `check_cancellation_safety.py` and the beta release guard pass. The beta guard sees no release-managed version delta against main.

The active change and both owning specifications pass strict OpenSpec validation. Whole-repository strict validation reports 37 passing and 22 failing specifications; the failing set matches an untouched export of current main exactly. No unrelated specification is changed to silence those failures.

No production server restart or new live-account test is part of this revision. Earlier live Codex 0.153.1 evidence belongs to the preceding implementation; current validation is the local integration evidence above plus the GitHub checks after publication.
