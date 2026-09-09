# Tasks

## 1. Sampling and estimator

- [x] 1.1 Add `ttft_cohort.py` with the module constants (1 h window, 64-sample cap, 8 samples / 3 accounts minimum, 20 000-token input cap, effort `None`/`minimal`/`low`, 10% upper trim, 15% deadband, 0.5 floor), `record_ttft_sample` (eligibility filter, bounded append at the balancer clock, lock-free), `account_ttft_estimate_ms` (upper-trimmed mean) and `ttft_weight_multiplier`.
- [x] 1.2 `RuntimeState.ttft_samples` / `ttft_weight`; `detached_runtime_snapshot` copies the samples so observe-only admission cannot write through.

## 2. Wiring

- [x] 2.1 `_write_request_log` records the sample from the funnel arguments (`status`, `request_kind`, TTFT, input and cached input tokens, reasoning effort, gate + bridge wait, `upstream_retried`); the WebSocket finalizer sets `upstream_retried` from `response_create_attempt_count > 1` (bridge retries), `replay_count > 0` (transparent direct-WebSocket replays) or a started account-capacity wait; `_acquire_request_state_response_create_admission` folds the global response-create admission wait into `latency_response_create_gate_wait_ms` so a direct WebSocket turn that queued on the saturated global limit is excluded like a bridge-queued one.
- [x] 2.2 `_build_states` applies `apply_ttft_cohort_weights` over the full runtime map after the states are built, compounding with the error-rate multiplier.
- [x] 2.3 Transition-gated `ttft_cohort_weight_change` INFO line without account identifiers; observe-only snapshot builds pass `log_weight_transitions=False` so the line is not repeated.

## 3. Documentation

- [x] 3.1 `docs/routing.md`: "Relative first-token latency weighting" paragraph after "Prefer earlier reset".
- [x] 3.2 `openspec/specs/account-routing/context.md`: rationale section (fleet-relative, no setting, 0.5 floor, trimmed mean, `routing_policy=preserve` alternative).

## 4. Verification

- [x] 4.1 Unit tests (`tests/unit/test_ttft_cohort_weighting.py`): eligibility filter per request kind / status / effort / input / queued wait, age and cap pruning, retried / capacity-wait rows and the uncached-input cap, trimmed-mean estimator on a bimodal mixture and a 30 s stall, deadband and floor, thin fleet evidence neutral, uniformly slow fleet neutral, prod-shaped cohorts, compounding with the error-rate multiplier, balancer-level steering and lift after the window clears, established `prompt_cache` owner kept and every deterministic strategy (`round_robin`, `sequential_drain`, `fill_first`, `usage_weighted`, `reset_drain`, `single_account`) unchanged, full-runtime fleet reference under narrowed selection and empty-runtime neutrality, request-log funnel records only eligible rows, WebSocket path (`tests/unit/test_websocket_ttft_sample_eligibility.py`, `tests/unit/test_websocket_upstream_transport_observability.py`): finalizer marks replayed and multi-attempt turns as retried, admission wait recorded into the gate wait and both kept out of the window, detached snapshot copy, transition log gating and identifier hygiene, no duplicate log from a detached-snapshot build.
- [x] 4.2 ruff, ty, proxy architecture / cancellation-safety / timing-seam / settings-tier guards (Settings stays 130, `load_balancer.py` at 2982/3021), strict OpenSpec validation, regression files for the touched modules.
