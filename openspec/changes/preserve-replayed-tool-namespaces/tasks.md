# Tasks: Preserve replayed tool namespaces

## 1. Implementation

- [x] 1.1 Keep namespace removal as an explicit compatibility operation in
  `_strip_unsupported_fields`, while disabling it for ChatGPT standard and
  compact payloads and for replay-safety projections.
- [x] 1.2 Preserve the existing explicit namespace sanitizer at the configured
  OpenAI-compatible model-source dispatch boundary.

## 2. Regression coverage

- [x] 2.1 Add unit coverage for standard and Lite/compact payloads, including
  the `(namespace, name)` identity and top-level namespace tool preservation.
- [x] 2.2 Add route-level WebSocket coverage for `function_call` and
  `custom_tool_call` replay items.
- [x] 2.3 Keep and run the model-source integration regression proving that the
  source payload still strips namespace while retaining compatible fields.

## 3. OpenSpec and verification

- [x] 3.1 Record the boundary change in the `responses-api-compat` delta and
  keep the proposal, design, context, and tasks internally consistent.
- [x] 3.2 Run focused pytest coverage, Ruff formatting/checks, and strict
  OpenSpec validation; inspect the final diff for whitespace and accidental
  changes.
