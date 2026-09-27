# Verification

## Confirmed failure and scope

A bounded, read-only production diagnostic for the reported old-conversation retry found twenty scoped references: nineteen all belonged to the same enabled, unchanged source revision; only a client-generated function_call_output.id was unknown. Its call_id belonged to that source. The presenting client key matched the prior conversation. The capture retained neither chat contents nor tokens. The passive diagnostic processes have stopped.

An independent synthetic public-endpoint smoke also reproduced the problem: an upstream streaming tool call succeeded, then its namespaced retained call plus a locally identified result returned HTTP 409 model_source_owner_unavailable before deployment. The synthetic request is retained privately for the after-deploy check. Preliminary non-stream probes were rejected upstream with HTTP 400; the matching streaming shape succeeded in obtaining a tool call. The stream's terminal output was empty, so the smoke collected output_item.done events as the client does.

A new route regression failed before the implementation with the same HTTP 409 on an output-only continuation. After the fix, the existing call-owner lookup remains mandatory while the validated local result ID is omitted only from the classification copy. No wire, subscription replay, database, source configuration or retry-policy changes were made.

## Validation

- 370 focused unit tests pass, including strict client result shapes, ownership extraction, unchanged subscription replay, wire preservation and existing portability/source-pool checks.
- 73 SQLite route tests pass (377.50s), including all new regressions and existing Codex client/source safety cases. 58 PostgreSQL 18 route tests pass (571.81s), including new result-ID cases and existing client-message regressions.
- Scoped and clean-checkout full Ruff lint, formatting and typing pass. Full dirty-workspace typing reports one unrelated existing error in tests/integration/test_proxy_chat_completions.py:109; that uncommitted file is excluded from this patch and the release checkout.
- Architecture, cancellation-safety and timing-seam guards pass.
- Strict OpenSpec change validation and all 65 main specs pass.
- Independent adversarial review: no actionable introduced regressions. The reviewer verified 449 tests, the 20-reference reproduction on all four routes, ownership/override probes, 12,000 helper mutations and lint/type checks. Malformed top-level type errors also reproduced against HEAD and were excluded as pre-existing. Review session: 01a0e143-c691-7833-9f7d-6f875891632b.
- Final scoped code/test hashes match the independently reviewed snapshot; git diff --check passes.
- All requirement scenarios are implemented and verified; no critical or warning verification issues remain in the scoped change. Main spec and context are synchronized.

Initial test setup corrections: the unit fixture mistakenly expected array output to be valid for apply_patch_call_output; it now uses the existing supported status/string shape. An initial PostgreSQL test startup raced test-container replacement and got connection errors; verification is rerunning after explicit readiness on a separate PostgreSQL 18 container. Neither correction changed application behavior.

## Requirement mapping

The added requirement's four scenarios are covered by tests/integration/test_source_pool_client_tool_results.py and tests/unit/test_source_client_tool_result_ids.py: all four public routes, streaming/non-streaming continuations, one-to-five-source expansion, a fresh replica, preserved result body, unknown/mixed call owners, unavailable/replaced/disallowed owners, client-key isolation, opaque/unknown fields, and item_reference checks after an accepted local result. Response-side IDs remain published. Code matches the design's strict shape exception and independent call ownership.
