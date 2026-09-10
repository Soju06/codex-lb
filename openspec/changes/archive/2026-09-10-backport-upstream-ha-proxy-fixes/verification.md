# Verification: selective upstream HA proxy fixes

Date: 2026-09-10. Local baseline: `7ecb38de`; upstream snapshot: `0f6a31c5`.
The key-dashboard baseline was pushed and independently checked with `git ls-remote` against `fork/feature/multi-file-account-import`. The backport remains uncommitted and undeployed.

## Completeness, correctness, and coherence

Six delta requirements (five added, one modified) are implemented across four existing capabilities. Canonical specs and stable context are synced; proposal, design, tasks, and source provenance are present. No unresolved implementation findings remain within this selected scope.

| Contract | Implementation | Verification evidence |
| --- | --- | --- |
| Per-replica client identity | `app/core/openai/model_refresh_scheduler.py`, fallback in `app/core/config/settings.py` | Follower warmup reaches real header normalization; failure still reconciles; stop cancels and awaits warmup; disabled scheduler performs no lookup; explicit fallback override is retained. |
| Discard inbound hints and synthesize trusted subscription hints | `app/core/clients/proxy.py`, `proxy_websocket.py`, selected-account streaming/compact/bridge openers | HTTP loopback wire tests, native WebSocket request capture, optional account header absent, LB-key auth, tier normalization/prohibition, actual versus requested tier, model-less preconnect, non-subscription forwarding, reconnect/fallback, reused sockets. |
| Rebuilt HTTP framing sanitation | Shared HTTP header builder | Fixed hop fields (including proxy authentication), Connection nominations, native identity classification, regenerated selected credentials/media types, preserved synthesized hint, canonical backend/v1 compact route tests. |
| Replica-local burst cooldown | `overload_backoff.py`, balancer runtime, stream health funnel | 5–30 second bounds and extension; fresh selection versus established sticky owner; single-candidate fallback; coded 429 unchanged; no persisted status/cooldown mutation; warning redaction. |
| Bounded same-owner burst recovery | Streaming retry mixin and API startup probe | 1/2/4 waits, Retry-After floor/cap, original 429 body/header, file and payload owners, single-account mode, post-refresh recovery, visible-output non-replay, consecutive wait markers, cancellation cleanup, settlement-before-health and unsuccessful settlement. |

## Final passing runs

All backend tests used the existing `.venv/bin/python` (actual interpreter: CPython 3.14.7), synthetic SQLite databases in a task-specific `/dev/shm` directory, mock/local-loopback upstreams, and no production credentials or database. Environment prefix:

```sh
env -u CODEX_LB_TEST_DATABASE_URL \
  CODEX_LB_ENV_FILE=/tmp/codex-lb-upstream-fixes.kvv6wL/no-env \
  CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN=upstream-disposable-token \
  TMPDIR=/dev/shm/codex-lb-upstream-fixes.V7ktpL \
  .venv/bin/python -m pytest -q <files> \
  --timeout=120 --timeout-method=thread --maxfail=5 --tb=short
```

- **2,696 passed**, `unit-final.xml`: complete `test_proxy_utils.py` and `test_proxy_http_bridge.py`, plus `test_codex_version.py`, `test_model_refresh_scheduler.py`, `test_http_subscription_routing_hint.py`, `test_responses_websocket_routing_hint.py`, `test_proxy_hop_by_hop_headers.py`, `test_proxy_upstream_fingerprint.py`, `test_websocket_upstream_transport_observability.py`, `test_failover_foundation.py`, `test_overload_backoff.py`, `test_responses_streaming_timeout_hardening.py`, and `test_streaming_retry_virtual_time.py` (all under `tests/unit/`).
- **130 passed**, `integration-final.xml`: complete `tests/integration/test_proxy_transient_retry.py`, `test_proxy_compact_hop_by_hop.py`, `test_proxy_compact.py`, `test_key_dashboard_api.py`, `test_key_dashboard_groups.py`, and `test_key_group_migration.py`. Includes backend/v1 and trailing-slash response error propagation and compact error contracts.
- **30 passed, 294 deselected**, bridge/WebSocket selection: `tests/integration/test_http_responses_bridge.py tests/integration/test_proxy_websocket_responses.py -k 'routing_hint or reconnect or tier or reuses_upstream_for_sequential_requests or confirmed_proxy_connect or bound or file_pin or settlement or cancel'`. One dependency deprecation warning about Starlette's AnyIO `BlockingPortal` alias; no failure.
- **7 passed**, `final-additions.xml`: new real API-key reservation exhaustion test, final file-pin assertion, and all hop-by-hop header tests after adding proxy-authentication fields. The real keyed backend request retries one account four times, returns the original HTTP 429 with Retry-After 5, and verifies persisted reservation state is closed before health is written. Six cases overlap the unit run; the keyed API case is additional.

Reports and logs are temporary local evidence under `/tmp/codex-lb-upstream-fixes.kvv6wL/`; they are not repository artifacts or production data.

## Static and spec checks

- `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`: pass.
- `.venv/bin/ty check --output-format concise`: pass.
- `.venv/bin/python scripts/check_proxy_architecture.py`: pass, thresholds unchanged.
- `.venv/bin/python scripts/check_cancellation_safety.py`: pass.
- `.venv/bin/python scripts/check_proxy_timing_seams.py`: pass.
- `.venv/bin/python .github/scripts/check_simplicity_budgets.py`: pass; no new settings or navigation items.
- `git diff --check`: pass.
- CI-pinned OpenSpec 1.11.0: `validate backport-upstream-ha-proxy-fixes --strict`: pass; `validate --specs --strict --json`: **59/59 pass**.

## Resolved findings during adaptation

- Upstream tests referenced a newer provider-session seam; they now isolate the existing `lease_http_session` instead of importing unrelated provider changes.
- Additional keyed-burst regressions exposed terminal account-health writes before reservation closure. Burst terminal failures now enter the existing deferred queue. Failed settlement/release does not apply the penalty; cancelled waits own release without penalizing the owner.
- Deferring unrelated initial 401 failures suppressed a later terminal rate-limit health write in an existing regression. The adaptation is limited to terminal burst 429s; the legacy non-burst refresh path is preserved and the full unit suites pass.
- Compact trailing-slash probes return the existing HTTP 405 contract. Tests pin that behavior and zero egress rather than introducing unsupported aliases.
- The two new facade arguments exceeded its existing line ceiling. The existing normalization call was compacted without changing behavior or raising the ratchet.

## Boundaries and limitations

- No full beta.6 merge, version bump, dependency/Rust update, configuration removal, schema change, migration reparenting, or runtime rollout. The deployed usage-group migration is unchanged; its upgrade/downgrade regression passes.
- Key-dashboard code, frontend assets, UTC+7 key reset implementation, native egress source and budgets, deployment scripts, and unrelated `simplify-install-one-liners` work are unchanged.
- No live ChatGPT/OpenAI request, production smoke, PostgreSQL matrix, rebuilt Rust helper, frontend rebuild, or complete repository-wide test suite was run for this backend-only backport. The focused transport tests use mocked native boundaries/local HTTP servers, not proof of live upstream acceptance.
- GitHub PR review/CI merge gates were not evaluated for the new uncommitted changes; local success is not a claim that a PR is merge-ready.
- Existing OpenSpec config emits an unknown `context_docs` rule warning while generating instructions; strict change and canonical spec validation still pass. No unrelated config repair was made.
