## 1. Regression coverage

- [x] 1.1 Update GPT-5.6 bootstrap catalog test evidence to cite
      `codex-rs/models-manager/models.json` at Codex `rust-v0.145.0`.
- [x] 1.2 Run the focused bootstrap metadata tests and verify every GPT-5.6
      entry reports `context_window` and `max_context_window` of 272,000.
- [x] 1.3 Assert the top-level `context_window` and raw
      `max_context_window` are 272,000 for every GPT-5.6 bootstrap entry.

## 2. Specification

- [x] 2.1 Add a `model-catalog-compat` delta that re-pins the GPT-5.6 bootstrap
      catalog source to Codex `rust-v0.145.0` and requires both context-window
      fields to be 272,000.
- [x] 2.2 State that operator context-window overrides take precedence over
      the default bootstrap budget.

- [x] 2.3 Correct the documented OpenCode and OpenClaw GPT-5.6 budgets to
      272,000 tokens.
## 3. Validation

- [x] 3.1 Validate the OpenSpec change and the complete specification set.
- [x] 3.2 Run the focused bootstrap metadata tests after review follow-up.
