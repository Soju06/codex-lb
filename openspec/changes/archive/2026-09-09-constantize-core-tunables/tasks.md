## 1. Settings surface
- [x] 1.1 Remove the 27 fields, the `image_inline_allowed_hosts` / `upstream_compact_timeout_seconds` validators and the `token_refresh_claim_ttl_seconds` model validator from `Settings`; add the 27 env names to `_REMOVED_SETTINGS`.
- [x] 1.2 Drop the `SETTING_TIERS` and `MIGRATING` rows in `app/core/config/tiers.py`; lower `[settings_fields].max` 130 -> 103.
- [x] 1.3 Record the `token_refresh_interval_days` exclusion (live canary-suite consumer) in the proposal.

## 2. Constants and consumers
- [x] 2.1 Transport: `MAX_SSE_EVENT_BYTES` / `UPSTREAM_RESPONSE_CREATE_MAX_BYTES` in `app/core/clients/proxy.py`; `_effective_compact_total_timeout` and `_compact_upstream_budget_seconds` become override-only; `_resolve_stream_transport` / `_ws_transport_payload_budget_bytes` lose the settings parameter.
- [x] 2.2 Auth: `OAUTH_TIMEOUT_SECONDS`, `TOKEN_REFRESH_TIMEOUT_SECONDS`, `_token_refresh_claim_ttl_seconds()`, `_REFRESH_FAILURE_COOLDOWN_SECONDS`, `ADMISSION_WAIT_TIMEOUT_SECONDS` (single home in `work_admission.py`, `service.py` duplicate removed).
- [x] 2.3 Usage: `USAGE_FETCH_TIMEOUT_SECONDS` / `USAGE_FETCH_MAX_RETRIES`, `app/core/usage/refresh_policy.py` (`USAGE_REFRESH_INTERVAL_SECONDS`, `usage_freshness_horizon_seconds()`), `_USAGE_REFRESH_AUTH_FAILURE_COOLDOWN_SECONDS`, `_REFRESH_INTERVAL_SECONDS` (reset credits); delete the `usage_refresh_enabled` gates and `ignore_refresh_disabled`; live ingestion always registers.
- [x] 2.4 Schedulers: sticky cleanup, model registry and quota planner builders pass `enabled=True`; `main.py` registry guards and the telemetry snapshot AND gate are unconditional; the timeout-invariant rule skip is removed and the admission-wait operand is a constant `_expr`.
- [x] 2.5 Ingress / images / models: `app/core/ingress_limits.py` shared by `request_body_limit.py` and `cli.py --ws-max-size`; inline image fetch branches and the host allowlist deleted; `DEFAULT_PUBLIC_IMAGE_MODEL`; prompt-cache-key derivation unconditional.
- [x] 2.6 Admission gates: `TOKEN_REFRESH_LIMIT`, `UPSTREAM_WEBSOCKET_CONNECT_LIMIT`, `COMPACT_RESPONSE_CREATE_LIMIT`; `WorkAdmissionController` keeps its kwargs.

## 3. Docs, chart, reference
- [x] 3.1 Regenerate `docs/reference/settings.md`.
- [x] 3.2 Helm: remove `config.promptCacheKeyDerivationEnabled` / `config.stickySessionCleanupEnabled` and their configmap lines; assert their absence in `tests/unit/test_helm_replica_artifacts.py`.
- [x] 3.3 OpenSpec deltas for every spec naming a removed setting or calling one of these values "configured" (incl. the `usage-refresh-policy` claim-TTL floor scenario, now a fixed-TTL scenario, and the `outbound-http-clients` / `proxy-runtime-observability` wording); `deployment-installation/context.md`, `quota-phase-planner/context.md`, `responses-api-compat/ops.md` prose.

## 4. Verification
- [x] 4.1 Tests inject through constants (`monkeypatch.setattr(module, "CONSTANT", ...)`) or constructor kwargs; `tests/unit/test_timeout_invariants.py` field list trimmed; `test_settings_trace_and_removed.py` covers the 27 names; request-path usage refresh is neutralised by the autouse fixture with the `usage_refresh_request_path` opt-out marker.
- [x] 4.2 Grep gate: no `.<field>` read or `<field>=` kwarg for the 27 fields remains under `app/`, `tests/`, `scripts/`.
- [x] 4.3 `make lint`, `uv run ty check`, `uv run pytest tests/unit -q -x`, touched integration files (incl. the native SSE probes with `CODEX_LB_NATIVE_EGRESS_TEST_BINARY`), `make migration-check`, `check_simplicity_budgets.py`, `helm template`, `openspec validate --specs --strict` and `openspec validate constantize-core-tunables --strict`.
