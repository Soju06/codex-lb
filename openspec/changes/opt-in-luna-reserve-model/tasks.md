## 1. Registry and policy

- [x] 1.1 Add the `base_model_inference` entry to `config/additional_quota_registry.json` with `display_label: Luna Reserve`, `model_ids: [gpt-reserve]`, `applies_to_plans: [plus, pro]`, the observed `limit_name` and `metered_feature` aliases, and `routing_policy: disabled`.
- [x] 1.2 Add `disabled` to `ADDITIONAL_QUOTA_ROUTING_POLICIES` in `app/modules/usage/additional_quota_keys.py` so the registry loader stops normalizing it to `inherit`.
- [x] 1.3 Drop the duplicate policy set in `load_balancer.py` and import the canonical one, so the two cannot drift again.

## 2. Selection

- [x] 2.1 Resolve the effective policy for a gated request and refuse before the additional-limit filter when it is `disabled`, ahead of the reauthentication rescue.
- [x] 2.2 Add the `additional_quota_routing_disabled` error code and a message naming the quota's display label.
- [x] 2.3 Keep `disabled` out of the ranking-override path so it can never reach `AccountState.routing_policy`.
- [x] 2.4 Register the code in `_UNAVAILABLE_SELECTION_ERROR_CODES` and `_LOCAL_PROXY_ERROR_CODES`.

## 3. Model

- [x] 3.1 Publish `gpt-reserve` in the model registry bootstrap, listed rather than hidden, scoped to Plus and Pro.
- [x] 3.2 Keep the pricing entry and the `_GPT5_ALIAS_BASE_MODELS` entry.
- [x] 3.3 Send `supportsLunaReserve=true` on the usage fetch.

## 4. Dashboard

- [x] 4.1 Add the `disabled` option to the additional-quota routing policy schema and both select controls.
- [x] 4.2 Add the `Off` label to `en`, `ko`, and `zh-CN`.
- [x] 4.3 Capture before/after screenshots of the routing settings card for the PR body.

## 5. Tests

- [x] 5.1 Parse a captured upstream body through `UsagePayload.model_validate` and assert the reserve bucket resolves to the registry entry. Constructing the payload in Python would pass with a wrong wire key, which is the failure this guards.
- [x] 5.2 Assert the reserve ships `disabled` and that the loader does not normalize it away.
- [x] 5.3 Cover the selection gate end to end through `select_account`: refused by default, routable after an operator override.
- [x] 5.4 Assert `disabled` produces no ranking override.
- [x] 5.5 Assert an unrecognized reserve refusal classifies `non_retryable` and surfaces.

## 6. Removals

- [x] 6.1 Delete `app/core/usage/luna_reserve.py` and its process-global state.
- [x] 6.2 Remove the reserve fields from `RuntimeState` and `AccountState`, the `handle_quota_exceeded` hook, and `is_account_luna_reserve_active`.
- [x] 6.3 Remove the WebSocket and compact wire overrides and the downstream model-name replacement.
- [x] 6.4 Remove the usage-updater eligibility hook and the `rate_limit_upsell` parsing it fed.
- [x] 6.5 Remove the JSON-RPC unsupported-parameter retry: the codes were app-server JSON-RPC codes on a REST path and the behavior was never observed.

## 6b. Fallout found by running it

- [x] 6b.1 Widen the `AdditionalQuotaPolicy.routing_policy` pattern in the settings schema. It pinned the four old values, so `/api/settings` returned a 500 as soon as the registry shipped `disabled` — caught by starting the app, not by the unit tests.
- [x] 6b.2 Update the three existing usage-URL assertions for the `supportsLunaReserve` query string.
- [x] 6b.3 Collapse the duplicated refusal blocks in `_load_selection_inputs`; the architecture ratchet forbids raising `load_balancer_lines`.

## 7. Validation

- [x] 7.1 `uv run ruff check app/` and `ruff format`.
- [x] 7.2 Unit tests for the touched areas.
- [x] 7.3 `npm run typecheck` and the routing-settings frontend test.
- [x] 7.4 `openspec validate --specs --strict`.
- [x] 7.5 `uv run pre-commit run local-ci --hook-stage manual --all-files` — ran; this change's suites (ruff, unit, integration, typecheck, frontend) pass. The gate additionally trips `check_migration_topology` on a pre-existing dual-head Alembic fork on `origin/main` (`20260914_000000_add_scim_tokens` #2431 vs `20260914_000000_drop_subscription_overflow_schema` #2422); this branch adds 0 migrations, so that failure is unrelated.
