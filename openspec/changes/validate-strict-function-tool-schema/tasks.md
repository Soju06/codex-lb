# Tasks

## Implementation

- [x] T1: Add `validate_strict_function_tool_schema(schema, *, name, param)` to `app/core/openai/strict_schema.py` returning a `StrictSchemaError | None` with `code=invalid_function_parameters`, caller-supplied `param`, message `"Invalid schema for function '<name>': In context=<path>, <reason>."`.
- [x] T2: Add `enforce_strict_function_tools_format(tools, *, param_template, nested)` to `app/modules/proxy/request_policy.py` that walks the supplied tools list, identifies `{type: "function", strict: True}` entries, calls T1's validator on `tool.parameters`, and raises `ClientPayloadError` on the first violation. Wire it into `normalize_responses_request_payload()` (default template `tools[{index}].parameters`, `nested=False`), the `/v1/responses` handler in `app/modules/proxy/api.py:v1_responses` (same default), and the `/v1/chat/completions` handler in `v1_chat_completions` with the chat-style template `tools[{index}].function.parameters` and `nested=True` — and crucially against the *raw* `payload.tools` list (before `to_responses_request()` runs `_normalize_chat_tools`) so the reported index always aligns with the inbound payload (codex review feedback on PR #658).
- [x] T3: Preserve the `strict` field in `_normalize_chat_tools()` (`app/core/openai/chat_requests.py`): when the inbound `tool.function.strict` is `True`/`False`, surface it on the normalized output dict so the responses-side enforcement applies to chat-completions traffic via the existing `to_responses_request()` coercion pipeline.

## Spec

- [x] T4: `MODIFIED Requirement: Strict-mode schema pre-validation` in `openspec/changes/validate-strict-function-tool-schema/specs/responses-api-compat/spec.md` requiring strict pre-validation on `tools[].parameters` when `tools[].strict is true`. Scenarios: missing `additionalProperties`, `additionalProperties:true`, missing `required` key, compliant strict schema accepted, `strict:false` skipped.
- [x] T5: `ADDED Requirement: Preserve function tool strict flag` in `openspec/changes/validate-strict-function-tool-schema/specs/chat-completions-compat/spec.md`. Scenarios: compliant strict tool 200, violating strict tool 400, strict=false preserved, omitted strict has no key, built-in tools unaffected.

## Tests

- [x] T6: `tests/unit/test_strict_schema_validation.py` — six new cases for `validate_strict_function_tool_schema`: missing additionalProperties, additionalProperties=true, missing required, valid schema, nested violation, anonymous name fallback.
- [x] T7: same file — six new cases for `enforce_strict_function_tools_format`: rejection envelope (code/param/type/message), correct index reporting, strict=false skipped, omitted strict skipped, compliant strict accepted, chat-style param template.
- [x] T8: same file — four new cases for chat coercion: strict=true preserved, strict=true violating pre-validated via responses pipeline, strict=false preserved, omitted strict has no key.
- [x] T9: `tests/integration/test_openai_compat_features.py` — `test_v1_chat_completions_rejects_strict_function_tool_violation` and `test_v1_responses_rejects_strict_function_tool_violation` confirming both endpoints return `400 invalid_function_parameters` with the correct `param` shape.

## Verify Script

- [x] T10: Update `scripts/verify_v1_responses_openai_sdk.py::case_tool_call_streaming` to set `parameters.additionalProperties = False`. Live re-run against the dev container — 5/5 PASS deterministically.

## Validation

- [x] T11: `openspec validate validate-strict-function-tool-schema` → "is valid".
- [x] T12: Targeted sweep (per `codex-lb-development` skill §5): 241 passed.
- [x] T13: SDK e2e (`tests/e2e/test_v1_responses_openai_sdk.py tests/e2e/test_openai_sdk_compat.py`): 20 passed.
- [x] T14: `uvx ruff check . && uvx ruff format --check . && uv run ty check`: all green.
- [x] T15: Live four-cell matrix against the local docker container (`codex-lb-dev-server`) with a real account: 8/8 PASS (A→400 with correct code+param on both endpoints; B/C/D→200 on both).
- [x] T16: Regression for codex review feedback on PR #658 — shifted-index scenario where `_normalize_chat_tools` drops an earlier entry. Added `test_chat_strict_violation_param_uses_original_index_when_normalizer_drops` (unit) and `test_v1_chat_completions_strict_violation_param_uses_original_index` (integration), plus a live 5th matrix cell (E) that posts a dropped tool at `[0]` and a strict-violating tool at `[1]`, asserting `param == "tools[1].function.parameters"`. Live result: 9/9 PASS (8 original + 1 new) plus verify script 5/5 PASS.
