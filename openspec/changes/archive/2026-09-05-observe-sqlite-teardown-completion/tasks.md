## 1. Confirm the real teardown ordering

- [x] 1.1 Extend `tests/unit/test_db_session.py` with a real file-SQLite worker-completed-during-loop-starvation regression for rollback and close, recording completion before loop resumption and independent writer progress.
- [x] 1.2 Obtain red sensitivity by disabling completion grace in the bounded executable check, then restore the production path; exercise asyncio and uvloop without fake rollback/close implementations.

## 2. Simplify without weakening ownership

- [x] 2.1 Reuse `_shielded_bounded` inside `_teardown_completed_after_bound`, returning True only for successful completion and preserving failed/cancelled/pending reclamation.
- [x] 2.2 Keep the held-connection snapshot, closed-handle skip, session fence, cleanup registry, late finalization and shutdown drain; update contradictory docstrings/log prose without new mechanisms.
- [x] 2.3 Keep warning event names and phase/bound/pre-grace elapsed fields; describe attempted or failed cleanup without claiming measured lag, guaranteed release or a permanent hold from insufficient evidence.

## 3. Validate and prepare delivery

- [x] 3.1 Run `uv run pytest tests/unit/test_db_session.py tests/unit/test_defer_cancellation_shield_leak.py tests/unit/test_graceful_shutdown.py -q`, including the new real-worker nodes; run `make lint` and `make typecheck`.
- [x] 3.2 Use CI-pinned OpenSpec1.11.0 to validate this change, the sibling `bound-sqlite-wedged-teardown`, and the owning `database-backends` capability strictly; run CI-equivalent `validate --specs`. Run full `validate --specs --strict` and record the 22 inherited placeholder-Purpose warnings whose spec files match pinned main exactly.
- [x] 3.3 Refresh the exact PR worktree index/content witness, run `gitnexus detect-changes --scope all --repo /Users/dpearson/repos/codex-lb/.agents/worktrees/pr-2030`, confirm only DB product/test changes remain relative to pinned main, and commit the cohesive remediation locally.
- [x] 3.4 Supply this accepted input to the combined integration and complete DB worker completion, cancellation/reclamation and shutdown ownership acceptance. Final wheel packaging and isolated built-product launch remain separate phase-five delivery gates.

- [x] 3.5 Verify, sync and archive the change after combined implementation acceptance, using CI-pinned OpenSpec1.11.0.

## 4. Address the remaining PR review comments

- [x] 4.1 Rename the change to match its DB-only scope, clarify the invalidation diagnostic owner, and document the caller-owned grace window before cleanup registration.
- [x] 4.2 Extend the existing failed-after-release regression to reject the misleading late-finish diagnostic, obtain red evidence, and use neutral completion wording without changing cleanup ownership.
- [x] 4.3 Run the guarded focused DB/cancellation/shutdown checks, lint, typecheck and strict owning OpenSpec validation; verify, sync and archive the renamed change.
