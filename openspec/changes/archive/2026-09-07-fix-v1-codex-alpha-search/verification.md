# Verification: v1 standalone Codex search

## Completeness and coherence

The implementation adds one `v1_router.post` registration to the existing
`codex_alpha_search` handler in `app/modules/proxy/api.py`. Both routers use the
same authentication and OpenAI error-format dependencies. The shared handler
continues through the existing capability guard and Codex control-request service.
The implementation matches `design.md`; no new configuration or transport logic
was introduced.

The main standalone-search requirement exactly matches the delta requirement,
and stable context is synced to `openspec/specs/responses-api-compat/context.md`.

## Correctness and regression coverage

| Contract | Evidence |
| --- | --- |
| Public v1 route fixes the observed failure | Before the code change, the v1 route test failed because no route was registered, and the public integration request returned 405 instead of 200. |
| Equivalent paths preserve forwarding | `test_codex_alpha_search_forwards_request_and_response` covers canonical, v1, and doubled-prefix routes; status 200/202, exact body bytes, duplicate query parameters, account-scoped credentials, session headers, and safe response headers. |
| Authentication fails before forwarding | `test_codex_alpha_search_requires_valid_proxy_credentials` covers missing and invalid credentials on all three paths. |
| Final upstream errors retain their envelope | `test_codex_alpha_search_preserves_normalized_control_error_contract` covers all three paths. |
| Unsupported methods do not forward | Unit route registration checks and `test_codex_alpha_search_unsupported_methods_do_not_forward` cover the POST-only contract. |
| Trailing slashes preserve current behavior | `test_codex_alpha_search_trailing_slash_is_rejected` checks 405, the exact error envelope, no redirect, and no upstream call. |
| Capability restrictions remain shared | The Daybreak capability route inventory and provider-routing matrix include the v1 route and doubled-prefix rewrite. |

## Validation

- Ruff lint and format checks pass for all four changed Python files.
- `git diff --check` passes.
- OpenSpec strict validation passes for the change and modified capability.
- Whole-repository OpenSpec validation retains the pre-existing failures detailed
  in `context.md`; every failing spec is unchanged from `HEAD`.
- The combined route, upstream-client, extended-proxy, and capability integration
  suite passed: **317 passed in 668.79 seconds**.

```sh
.venv/bin/pytest -q tests/unit/test_codex_alpha_search_route.py \
  tests/unit/test_codex_upstream_paths.py \
  tests/integration/test_proxy_api_extended.py \
  tests/integration/test_daybreak_capability_routes.py
```

The change has no outstanding implementation or test findings. The unrelated
repository-wide OpenSpec validation failures remain recorded above.

## Runtime scope

Production logs and unauthenticated route probes confirmed the missing route.
Successful upstream behavior is verified with test doubles; this change has not
been deployed or exercised with a live authenticated production search.
