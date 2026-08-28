## 1. Schema Regression

- [x] 1.1 Add a real unauthenticated `/openapi.json` regression that asserts every documented HTTP operation has a globally unique `operationId` and pins the exact thread-goal GET and POST identifiers.
  - Evidence: `test_openapi_operation_ids_are_unique_and_thread_goal_methods_stable` requests `/openapi.json`, enumerates all documented HTTP methods, reports duplicate counts, and pins both target IDs.
- [x] 1.2 Run the focused schema regression before the production fix and record the expected duplicate-identifier failure.
  - Evidence: pre-fix `uv run pytest tests/integration/test_proxy_api_extended.py::test_openapi_operation_ids_are_unique_and_thread_goal_methods_stable -q` failed with one duplicate group, `thread_goal_get_backend_api_codex_thread_goal_get_get: 2`.

## 2. Route Registration

- [x] 2.1 Replace the thread-goal multi-method `api_route` decorator with adjacent GET and POST decorators on the same handler, without an explicit `operation_id`.
  - Evidence: `app/modules/proxy/api.py` now stacks `@router.get("/thread/goal/get")` and `@router.post("/thread/goal/get")` directly on the unchanged `thread_goal_get` handler.
- [x] 2.2 Confirm the schema regression passes and the existing parameterized GET/POST runtime forwarding parity test remains unchanged and green.
  - Evidence: focused pytest for the schema regression, the hermetic route-registration unit regression, and `test_thread_goal_get_forwards_upstream_goal` completed `4 passed`; the existing parameterized parity test body is unchanged.

## 3. Verification

- [x] 3.1 Run focused Ruff and type checks for the touched Python scope.
  - Evidence: `uv run ruff check app/modules/proxy/api.py tests/integration/test_proxy_api_extended.py tests/unit/test_thread_goal_route.py` returned `OK`; `uv run ty check` on the same files returned `All checks passed!`.
- [x] 3.2 Validate the change and main OpenSpec specifications strictly, then verify implementation, scenarios, design, and task coherence.
  - Evidence: `openspec validate thread-goal-openapi-operation-ids --strict` passed; `openspec validate --specs --strict` reported `47 passed, 0 failed`; `/opsx:verify` mapped all three scenarios to the schema regression or unchanged runtime parity test with no design divergence.
