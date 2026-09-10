## 1. Durable transcript reconstruction

- [x] 1.1 Raise the default bounded transcript walk to 256 turns.
- [x] 1.2 Preserve descendant turns when a complete replay snapshot replaces
  the oldest retained ancestor.
- [x] 1.3 Keep the existing transcript byte and item limits across the merged
  snapshot and descendant tail.

## 2. Replay compatibility

- [x] 2.1 Strip only known legacy response-owned reasoning and metadata fields.
- [x] 2.2 Normalize provider-owned output annotations and empty tool-output
  fragments without accepting unknown shapes.
- [x] 2.3 Keep account-neutral tool-call settlement and duplicate suppression
  fail-closed.

## 3. Regression coverage and validation

- [x] 3.1 Cover legacy snapshot bookkeeping and output normalization.
- [x] 3.2 Cover snapshot-root replay with a descendant tool-call tail.
- [x] 3.3 Run focused transcript and HTTP bridge tests, Ruff, and Ty.
- [x] 3.4 Run strict OpenSpec validation before publishing the change.
