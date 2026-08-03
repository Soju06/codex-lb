## 1. Regression coverage

- [x] 1.1 Add unit tests proving standard and compact wire payloads omit replayed tool-call namespaces while request input retains local metadata.
- [x] 1.2 Add public `/v1/responses` and WebSocket `response.create` integration coverage for namespaced `function_call` and `custom_tool_call` replay payloads, and confirm top-level namespace tools remain preserved by existing coverage.
- [x] 1.3 Run the focused regression tests before implementation and confirm they fail for the missing normalization.

## 2. Implementation

- [x] 2.1 Normalize recognized replayed tool-call input items in the shared outbound sanitizer by copying affected items and removing only `namespace`.
- [x] 2.2 Run focused unit and integration tests and complete the regression assertions.

## 3. Validation

- [x] 3.1 Run formatter, linter, focused tests, and relevant broader request/proxy tests.
- [x] 3.2 Run strict change validation, repository spec validation, OpenSpec verification, and inspect the final diff.
