## 1. Specification

- [x] 1.1 Replace the "Compaction request is not source-routed" scenario with
      the refusal contract and add the refusal requirement for the standalone
      compact routes.

## 2. Implementation

- [x] 2.1 Add `_model_source_compaction_denial` beside
      `_disabled_model_source_denial`, sharing the subscription-registry fast
      path, returning HTTP 400 `compaction_unsupported`.
- [x] 2.2 Call it on `POST /backend-api/codex/responses` when the source-route
      exclusion is caused by a terminal `compaction_trigger`, before source
      selection, admission, reservation, and the compact flow.
- [x] 2.3 Call it in `_compact_responses` after model access validation and
      before admission and limit enforcement, for both compact routes.

## 3. Regression coverage

- [x] 3.1 Route coverage: source model plus terminal trigger on the Codex
      Responses route answers 400 `compaction_unsupported`, the source upstream
      receives nothing, and no subscription account selection runs.
- [x] 3.2 Route coverage for both standalone compact routes.
- [x] 3.3 Negative controls: the same source model without a trigger still
      streams through the source; a subscription-registry model still reaches
      the compact flow even when a source lists the same slug.

## 4. Validation

- [x] 4.1 Confirm the route regressions fail on the baseline and pass with the
      change; run the focused compact/responses/source-routing suites, Ruff,
      ty, the proxy architecture check, and strict OpenSpec validation.
