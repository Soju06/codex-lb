## 1. Compact preparation

- [x] 1.1 Elide inline data-URL image bytes only when compact input is oversized.
- [x] 1.2 Preserve tool call/output identity, text parts, and accepted `input_file` references.
- [x] 1.3 Retain fail-closed handling for oversized required non-image content.
- [x] 1.4 Retain fail-closed handling for hosted computer screenshots whose schema cannot carry a text marker.

## 2. Validation

- [x] 2.1 Add the exact required-latest-tool-output regression.
- [x] 2.2 Add negative controls for accepted `input_file` references and hosted computer screenshots.
- [x] 2.3 Prove the terminal compact route; retain live helper-deployed smoke as rollout evidence.
