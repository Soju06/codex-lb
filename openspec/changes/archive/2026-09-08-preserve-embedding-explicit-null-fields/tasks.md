## 1. Regression Coverage

- [x] 1.1 Add the exact ASGI source-capture regression for explicitly supplied null `dimensions` and `user` fields and confirm it fails because the captured payload omits those keys.
- [x] 1.2 Add an omitted-fields control that proves unsent extras remain absent.
- [x] 1.3 Extend the existing non-null embeddings settlement test to prove extra values, reservation settlement, and request-log metadata remain unchanged.

## 2. Serialization Fix

- [x] 2.1 Change only source-routed embeddings serialization to omit unset fields while preserving explicit null values.
- [x] 2.2 Run the explicit-null regression and omitted/non-null accounting controls to green.

## 3. Contract and Verification

- [x] 3.1 Sync the stable embeddings field-presence and accounting requirement into the owning `model-source-routing` main spec.
- [x] 3.2 Run focused model-source routing, forwarding, and request-log tests plus relevant Ruff and type checks.
- [x] 3.3 Run strict OpenSpec validation and verify the implementation against all change artifacts.
