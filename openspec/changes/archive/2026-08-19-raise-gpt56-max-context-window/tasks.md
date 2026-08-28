## 1. Bootstrap catalog

- [x] 1.1 Add `max_context_window: 872_000` to `_gpt56_raw()` so it overrides
      the `_bootstrap_model` synthesis for all three GPT-5.6 slugs at once.
- [x] 1.2 Leave `context_window` at 272,000 for Sol, Terra, and Luna.
- [x] 1.3 Amend the `_gpt56_raw()` docstring to record `max_context_window` as
      the one tracked divergence from the `rust-v0.145.0` base pin, citing the
      upstream commit.

## 2. Regression coverage

- [x] 2.1 Assert `max_context_window == 872_000` and `context_window ==
      272_000` for every GPT-5.6 entry in the shared unit-test loop.
- [x] 2.2 Assert the same pair through `GET /backend-api/codex/models` in the
      shared integration-test loop.
- [x] 2.3 Assert `max_context_window > context_window` in both loops so a
      future re-unification of the two fields fails loudly.
- [x] 2.4 Assert `GET /v1/models` still reports 272,000 for the GPT-5.6 input
      budget fields on the bootstrap path.
- [x] 2.5 Re-pin both evidence comments to `rust-v0.145.0` plus the upstream
      commit for `max_context_window`.

## 3. Documentation

- [x] 3.1 Distinguish the 272k default budget from the 872k maximum in the
      client-setup model lineup summary.
- [x] 3.2 Document the Codex CLI opt-in with correct clamp semantics: values
      above `max_context_window` are clamped, and the auto-compact limit
      resolves to 90% of the window, so larger values are no-ops.
- [x] 3.3 Leave the OpenCode and OpenClaw examples at 272,000, matching what
      `/v1/models` advertises.

## 4. Specification

- [x] 4.1 Add a `model-catalog-compat` delta requiring `context_window`
      272,000 and `max_context_window` 872,000 for the GPT-5.6 bootstrap
      entries.
- [x] 4.2 Restate the requirement and surviving scenarios in full so the delta
      is order-insensitive against the unarchived `fix-gpt56-context-window`
      delta.
- [x] 4.3 Carry GIVEN clauses on context-budget scenarios excluding refreshed
      snapshots, persisted snapshots, and operator context-window overrides.

## 5. Validation

- [x] 5.1 `openspec validate raise-gpt56-max-context-window --strict`
- [x] 5.2 `openspec validate --specs`
- [x] 5.3 Focused unit + integration model-catalog tests, ruff, and
      `mkdocs build --strict`.
