## 1. Compact preparation

- [x] 1.1 Elide inline data-URL image bytes only when compact input is oversized.
- [x] 1.2 Preserve tool call/output identity, text parts, and file-backed images.
- [x] 1.3 Retain fail-closed handling for oversized required non-image content.

## 2. Validation

- [x] 2.1 Add the exact required-latest-tool-output regression.
- [x] 2.2 Add a negative control for file-backed image references.
- [x] 2.3 Prove the terminal compact route; retain live helper-deployed smoke as rollout evidence.
