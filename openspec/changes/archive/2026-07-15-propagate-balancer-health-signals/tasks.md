# Tasks: propagate-balancer-health-signals

- [x] 1. Scaffold `openspec/changes/propagate-balancer-health-signals/` (proposal, design, tasks, context, account-routing deltas) and validate.
- [x] 2. `app/core/balancer/logic.py`: add `RATE_LIMITED_MIN_COOLDOWN_SECONDS = 30.0`; in `handle_rate_limit`, when `_extract_reset_at(error)` is `None`, set `state.reset_at = now + delay` (Retry-After delay verbatim; backoff fallback floored at the constant). Local `cooldown_until` behavior unchanged. Export the constant from `app/core/balancer/__init__.py`.
- [x] 3. `app/modules/proxy/load_balancer.py` `_state_from_account`: when `status_seed` is `RATE_LIMITED`, `effective_runtime_reset` is `None`, and `effective_blocked_at` is set, synthesize `effective_runtime_reset = effective_blocked_at + RATE_LIMITED_MIN_COOLDOWN_SECONDS` while now is inside that window (legacy-row floor). Verified `background_recovery_state_from_account` needs no change (it already seeds `cooldown_until` from persisted `reset_at`).
- [x] 4. Verify synthetic `reset_at` cannot trip the reset-confirmed limit warm-up trigger (it compares usage-window entries, not `accounts.reset_at`) and that quota presentation renders the persisted deadline sanely; findings recorded in design.md/context.md.
- [x] 5. Unit tests: floored deadline persisted for metadata-free 429; Retry-After deadline persisted verbatim; upstream reset metadata still wins; legacy-row floor in `_state_from_account`.
- [x] 6. Integration regression tests at the two-replica selection path (`tests/integration/test_load_balancer_multi_replica.py`): flip-back regression, Retry-After propagation, legacy-row floor hold and post-floor recovery.
- [x] 7. Run targeted pytest + ruff; `openspec validate propagate-balancer-health-signals`.
- [x] 8. Codex review follow-up: in both `_state_from_account` early-recovery gates, require the runtime block marker to be at least as recent as the effective persisted `blocked_at`, so leftover runtime cooldown state from an earlier 429 cannot unlock early recovery of a peer's newer block (unit + two-replica regression tests).

Follow-ups intentionally not in this change (see design.md): round-robin replica decorrelation; staleness-first usage-refresh selection.
