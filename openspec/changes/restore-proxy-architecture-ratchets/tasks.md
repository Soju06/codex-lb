## 1. Baseline and characterization

- [x] 1.1 Create a replacement implementation branch from current `main`, port PR #1416's focused `service.py` / `_service/support.py` diff, and record in the eventual PR that it supersedes #1416; leave closure of the older PR to a maintainer.
  - Draft PR #1430 uses the replacement branch, ports PR #1416's code move exactly, records the supersession relationship, and leaves closure of #1416 to a maintainer.
- [x] 1.2 Add or identify public-boundary `LoadBalancer.select_account()` characterization cases for non-sticky and sticky success, required-owner conflict, ambiguous ownership, hard-affinity saturation, account-cap exhaustion, stale persistence retry, and cancellation/exception lease cleanup; additionally assert cache-generation reselection on the non-sticky path and exactly-once lease release wherever either path has acquired a lease.
- [x] 1.3 Run the focused characterization set before code movement and record the passing baseline and current architecture-check failures.
  - Before movement, the focused contract/concurrency baseline passed 57/57; the checker reported `service.py` 2,604/2,600, `load_balancer.py` 3,260/3,021, and `select_account()` 699/527.

## 2. Restore the proxy boundaries

- [x] 2.1 Complete the focused support extraction from `service.py` while preserving every required façade export and compatibility import covered by the architecture checker.
- [x] 2.2 Add `app/modules/proxy/_load_balancer/sticky_selection.py` with a narrow owner protocol plus typed request/outcome carriers, including updated selection inputs, an explicit terminal/direct-error disposition, and a typed callback for shared metrics side effects such as `_record_account_cap_rejection`; do not import `LoadBalancer` back into the private module.
- [x] 2.3 Move the sticky-key retry loop, the existing self-free `_select_with_stickiness()` helper, and its smallest cycle-free pure policy closure (`_select_account_preferring_budget_safe`, both budget-threshold predicates, `_best_health_tier_states`, `_filter_states_for_account_caps`, and account-cap error code/message helpers) behind that boundary; keep shared metrics recording outside the pure closure, preserve explicit re-exports used by current tests/importers plus legacy-row precedence, mobility provenance, hard-affinity fail-closed behavior, mapping preservation, cap spillover, stale-row retries, and lease cleanup, then rerun its characterization cases.
- [x] 2.4 Keep shared preflight, the no-sticky retry path, and final result construction in `LoadBalancer.select_account()`, then confirm `service.py`, `load_balancer.py`, and the public method all satisfy their existing thresholds without raising a ratchet.

## 3. Improve architecture diagnostics

- [x] 3.1 Refactor `scripts/check_proxy_architecture.py` to collect independent assertion failures in stable order, retain each existing message, and return one non-zero status after printing every violation.
- [x] 3.2 Add checker regression coverage proving that two simultaneous fixture violations are both reported, dependent AST checks degrade safely after a parse failure, a clean fixture exits zero, and the repository's real files pass.

## 4. Verification and handoff

- [x] 4.1 Run `uv run pytest tests/unit/test_load_balancer.py tests/unit/test_load_balancer_contract.py tests/unit/test_load_balancer_concurrency.py tests/unit/test_proxy_load_balancer_refresh.py` and confirm all public selection contracts pass.
  - Result: 374 passed, 3 pre-existing skips.
- [x] 4.2 Run `uv run pytest tests/integration/test_load_balancer_integration.py tests/integration/test_load_balancer_multi_replica.py tests/integration/test_proxy_sticky_sessions.py` and confirm persistence, replica, and sticky-session paths pass.
  - Result: 39 passed.
- [x] 4.3 Run `python3 scripts/check_proxy_architecture.py`, changed-file Ruff format/lint, and `uv run ty check`; confirm all existing thresholds pass and no private-package import cycle is introduced.
  - The architecture checker, Ruff, format, and scoped ty all pass. The global ty run reports only four unresolved `_analytics` imports in pre-existing untracked `.codex/hooks/` files outside this change.
- [x] 4.4 Run `openspec validate restore-proxy-architecture-ratchets --strict` and `openspec validate --specs`, then verify the implementation against this change before requesting current-head CI and Codex review.
