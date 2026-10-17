## 1. Refresh-scoped transition evidence

- [x] 1.1 Snapshot the selected account's plan before background usage refresh.
- [x] 1.2 Pass the pre-refresh plan map and refresh timestamp into long-window warm-up evaluation.

## 2. Monthly transition candidate

- [x] 2.1 Add a paid-to-Free fallback candidate that requires a fresh available monthly sample.
- [x] 2.2 Preserve ordinary same-window reset detection and the existing durable monthly claim.

## 3. Regression coverage

- [x] 3.1 Prove a confirmed paid-to-Free scheduler refresh sends one monthly warm-up regardless of prior usage.
- [x] 3.2 Cover unconfirmed or unchanged Free plans, stale monthly history, availability gating, and deduplication.

## 4. Validation

- [x] 4.1 Run focused scheduler and limit warm-up tests.
- [x] 4.2 Run Ruff format/check, Ty, strict OpenSpec validation, and diff hygiene checks.
