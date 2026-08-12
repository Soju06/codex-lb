## 1. Regression coverage

- [x] 1.1 Update GPT-5.6 bootstrap catalog test evidence to cite
      `codex-rs/models-manager/models.json` at Codex `rust-v0.145.0`.
- [x] 1.2 Run the focused bootstrap metadata tests and verify every GPT-5.6
      entry reports `context_window` and `max_context_window` of 272,000.

## 2. Specification

- [x] 2.1 Add a `model-catalog-compat` delta that re-pins the GPT-5.6 bootstrap
      catalog source to Codex `rust-v0.145.0` and requires both context-window
      fields to be 272,000.

## 3. Validation

- [x] 3.1 Validate the OpenSpec change and the complete specification set.
