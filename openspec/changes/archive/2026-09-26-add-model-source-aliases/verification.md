# Verification: model source aliases

Initial verification ran on 2026-09-26 against the working tree. Production source configuration, pinned client catalog and deployment remain unchanged. Commit preparation was separately verified below.

## Completeness and correctness

All four added requirements have implementation and regression coverage:

- Explicit mapping: validated `upstream_model` metadata; mapping is applied once in the HTTP source transport after selection. Route tests cover Responses JSON/SSE on both canonical and trailing-slash backend/v1 paths, Chat, Embeddings and multipart Audio. Existing unsupported trailing-slash routes retain 405. Invalid create/update is atomic, API-key model/source restrictions hold, and a disabled source is rejected before dispatch.
- Public response identity: structured model fields are translated, with text and tool arguments unchanged. Tests cover legal multiline data, CR/LF/CRLF, fragmented Unicode/BOM, bounded buffering and upstream cleanup. Disconnect tests prove reservation release when no usage/content was delivered. Existing stream lifecycle tests also pass.
- Dashboard: mixed identity/alias entries, invalid and duplicate entries, metadata/capability/pricing/disabled-state preservation when renaming, pricing edits and explicit mapping removal are covered.
- Catalog: both native Codex and OpenAI catalogs expose the public alias and hide routing metadata while retaining base instructions and multi-agent namespace capability. Request logs, cost and reservations retain the public model. Follow-up requests preserve `previous_response_id`; alias resolution does not introduce or change ownership selection.

The implementation follows the design: no migration or environment setting, no recursive mapping, existing source selection/continuity ownership remains before translation. Transport dataclass replacement retains the original transport owner and direct close path.

## Validation results

- Backend regression: **351 passed** across alias unit/integration tests, source catalog/service/forwarding, routing and dispatch suites.
- Catalog/API-key compatibility: **48 passed, 137 deselected** using the existing source-focused tests in `test_api_keys_api.py` and `test_v1_models.py`.
- Frontend model source suite: **29 passed** across four test files.
- Scoped Python `ty check`, frontend TypeScript and ESLint: passed.
- Repository Ruff lint/format: passed (**1133 files** formatted).
- Architecture, cancellation-safety, proxy timing-seam and settings-tier checks: passed.
- `git diff --check`: passed.
- Strict OpenSpec change validation: passed. Main `model-source-routing` spec is valid.
- All-spec strict validation: **15 pre-existing failing specs**, with exactly the same error multiset before/after this change. No new errors. The model-catalog capability is among the pre-existing failures.
- Repository-wide `ty check`: one existing diagnostic in the unrelated dirty `tests/integration/test_proxy_chat_completions.py:109` (`ProxyResponseError` receives a plain dict instead of `OpenAIErrorEnvelope`). Scoped alias implementation/tests pass.

## Visual evidence and limitations

[Before](screenshots/before.png) and [after](screenshots/after.png) capture the create-source dialog at the same viewport, using a clean HEAD frontend for before and the working frontend for after. Browser API fixtures were local; unrelated settings panels had incomplete fixture responses. The alias form itself rendered and was visually inspected; form submission behavior is covered by the frontend tests.

Live production traffic and a real custom-endpoint Codex session were not exercised for this feature. Tests use recording local upstream HTTP stubs. Configure aliases only after every production replica runs this implementation, then refresh any pinned client model catalog.

No unresolved implementation findings. Stable requirements/context were synchronized to the owning main specs before archive.

## Commit preparation verification

On 2026-09-26, the alias-only staged tree was exported to an isolated clean
snapshot based on `951f9df5`. Unrelated working-tree edits were excluded,
including the new-account warm-up translation entries that share locale files.

- Alias unit and route regressions: **39 passed**.
- Frontend model source suite: **29 passed**.
- Repository-wide Ruff lint/format and Python type checking: **passed** in the
  clean snapshot. The unrelated dirty test diagnostic reported above is absent.
- Frontend TypeScript and scoped ESLint: **passed**.
- Final staged scope: alias code, tests, translations, owning specs, and the
  archived change including before/after screenshots. No production credentials
  or configuration are part of the commit.
