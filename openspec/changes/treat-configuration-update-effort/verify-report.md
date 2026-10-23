## Verification Report: treat-configuration-update-effort

### Summary

| Dimension    | Status                        |
|--------------|-------------------------------|
| Completeness | 15/15 tasks, 2 requirements   |
| Correctness  | 2/2 requirements covered      |
| Coherence    | No design.md; patterns match  |

### Completeness

- Tasks: 15 of 15 checked in `tasks.md`.
- Requirements: one MODIFIED (`responses-api-compat`) and one ADDED
  (`proxy-runtime-observability`).

### Correctness

`responses-api-compat` — "API-key reasoning allowlists reject disallowed explicit efforts" (13 scenarios):

| Scenario | Evidence |
|---|---|
| Reject max before upstream dispatch | `tests/unit/test_request_policy.py` allowlist rejection cases; `tests/integration/test_api_keys_api.py:2603` |
| Configuration update effort is authorized as the client-selected value | `tests/unit/test_request_policy.py:1119`; forwarded verbatim in `tests/integration/test_api_keys_api.py:865` |
| Disallowed configuration update effort is rejected before dispatch | `tests/unit/test_request_policy.py:1075`; `tests/integration/test_api_keys_api.py:815` (403, no upstream call) |
| Configuration update outranks a model alias | `tests/unit/test_request_policy.py:1100` (alias selects an allowed effort, update selects a disallowed one, request is rejected) |
| Enforced effort conflicts with a configuration update | `tests/unit/test_request_policy.py` enforced-conflict cases; `tests/integration/test_api_keys_api.py` rejection before dispatch |
| Alias effort is evaluated as the client-selected value | Pre-existing alias policy tests in `tests/unit/test_request_policy.py` |
| Omitted effort remains compatible | Pre-existing omitted-effort tests |
| Effort-less provider controls remain compatible | `tests/unit/test_request_policy.py:879` |
| Source-routed conflicting alias cannot override policy | `tests/unit/test_request_policy.py:868` |
| Blank alias cannot hide a disallowed effort | `tests/unit/test_request_policy.py` blank-effort cases |
| Disabled alias cannot hide an enabled effort | `tests/unit/test_request_policy.py:858` |
| Provider metadata cannot hide an enabled effort | `tests/unit/test_request_policy.py` implicit-thinking cases |
| Effort-less provider control survives beside an allowed effort | `tests/unit/test_request_policy.py:879` |

`proxy-runtime-observability` — "Request logs persist the effective client reasoning effort" (5 scenarios):

| Scenario | Evidence |
|---|---|
| Mid-conversation update is the logged effort | `tests/unit/test_request_policy.py:1008`; `tests/integration/test_proxy_responses.py:4328` |
| Last update wins | `tests/unit/test_request_policy.py:1049` |
| Enforced effort is the logged effort | `tests/integration/test_api_keys_api.py:742` (asserts the persisted effort equals the enforced one) |
| Requests without updates keep the request-level value | `tests/unit/test_request_policy.py:1137`; normalized fallback case in `tests/unit/test_proxy_utils.py` (`minimal` -> model minimum `medium`) |
| Client-plane aliases are logged in wire form | `tests/unit/test_request_policy.py:1067` (`ultra` -> `max`, `minimal` -> `low`, `xhigh` unchanged) |

Implementation mapping:

- Derivation and precedence: `app/modules/proxy/request_policy.py`
  (`_client_configuration_update_effort`, `_client_reasoning_effort`).
- Log mapping: `client_reasoning_effort_for_log`, used by the request-log call
  sites in `_service/streaming/retry.py`, `_service/streaming/mixin.py`,
  `_service/compact.py`, `_service/http_bridge/request_submit.py` and
  `_service/http_bridge/streaming.py`.
- Enforcement mirroring: the `enforced_reasoning_effort` branch sets
  `payload._codex_lb_client_reasoning_effort`, matching the `enforced_model`
  branch.
- Wire items: forwarded unchanged; asserted in both Responses integration tests.

### Coherence

- No `design.md` in this change, so there is no design decision to verify
  against (skipped, not failed).
- Code follows the existing patterns: one derivation helper beside the existing
  alias helpers, one log helper beside `resolve_wire_reasoning_effort`, no new
  setting, no new module.

### Issues by priority

1. **CRITICAL**: none.
2. **WARNING**: none.
3. **SUGGESTION**: none.

### Final assessment

All checks passed. Ready for archive.
