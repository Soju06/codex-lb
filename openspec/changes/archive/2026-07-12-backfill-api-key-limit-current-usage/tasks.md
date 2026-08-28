## 1. Specs

- [x] 1.1 Add an API-key update requirement for backfilling newly-added limit rules from current-window request logs.
- [x] 1.2 Validate OpenSpec changes.

## 2. Implementation

- [x] 2.1 Add repository support for aggregating API-key usage by limit type, window, and optional model filter.
- [x] 2.2 Initialize newly-added API key limit rules from the aggregate when `resetUsage` is false.
- [x] 2.3 Preserve existing matching limit values and explicit reset behavior.

## 3. Verification

- [x] 3.1 Add regression coverage for adding a total-token daily limit after existing usage.
- [x] 3.2 Add regression coverage for model-filtered limit backfill.
- [x] 3.3 Add regression coverage that `resetUsage=true` keeps new limits at zero.
- [x] 3.4 Run targeted tests and static checks.
