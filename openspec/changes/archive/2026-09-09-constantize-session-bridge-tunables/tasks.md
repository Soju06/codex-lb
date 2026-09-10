- [x] Add `HTTP_BRIDGE_IDLE_TTL_SECONDS`, `HTTP_BRIDGE_CODEX_IDLE_TTL_SECONDS` and
  `HTTP_BRIDGE_STUCK_GATE_RETIRE_AFTER_SECONDS` to `http_bridge/helpers.py`; read
  them through the module at call time (runtime config, session registry,
  stale-gate snapshot, eventless budget, sticky cleanup retention).
- [x] Collapse the anchor-poison threshold onto `_HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD`:
  delete `_http_bridge_effective_anchor_poison_threshold`, drop the
  `configured_threshold` parameter of `_http_bridge_poison_anchor_clear_owed`,
  and remove all 13 `getattr` seams.
- [x] Add `HTTP_BRIDGE_SERVER_RECOVERY_MAX_ATTEMPTS` (6) in `app/modules/proxy/api.py`.
- [x] Use the existing `_HTTP_BRIDGE_CLEAN_CLOSE_RETRY_JITTER_MAX_SECONDS` directly
  (`random.uniform(0, 2.0)`).
- [x] Delete the three `operation_ledger_enabled` branches (api eligibility,
  submit-path record gate, streaming recovery gate).
- [x] Remove the seven `Settings` fields; add the env names to
  `_REMOVED_SETTINGS`; drop `SETTING_TIERS` / `MIGRATING` rows; retier
  `http_responses_session_bridge_enabled` to T4.
- [x] `timeout_invariants.py`: the `2x stuck gate < bridge budget` rule reads the
  constant through `_expr`; regression test monkeypatches the constant.
- [x] Regenerate `docs/reference/settings.md`; lower `[settings_fields].max` to 96.
- [x] Helm: drop the idle-TTL keys/values; make pod identity env unconditional;
  update the chart README; Helm regression tests.
- [x] Convert bridge test seams to module-constant monkeypatches; add
  `_REMOVED_SETTINGS` coverage for the seven names.
- [x] Spec delta: REMOVED/ADDED "Repeated zero-event idle failures poison dead anchors"
  -> "... at the circuit threshold" (fixed threshold wording; the
  above-two-threshold bypass scenario is replaced).
- [x] Verification: `make lint`, `uv run ty check`, full bridge test files, unit
  suite, `make migration-check`, simplicity budgets, `helm template`,
  `openspec validate --specs --strict`.
