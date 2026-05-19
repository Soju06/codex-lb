## 1. Implementation

- [x] 1.1 Update sticky selection so backend `codex_session` mappings reallocate when the pinned account is above the sticky budget threshold and a healthier candidate exists
- [x] 1.2 Preserve existing durable `codex_session` behavior when the pinned account is still below the threshold
- [x] 1.3 Prefer budget-safe Responses routing candidates over pressured candidates when any budget-safe option exists

## 2. Verification

- [x] 2.1 Add integration coverage for backend Codex `session_id` routing that proves reallocation above threshold
- [x] 2.2 Run the affected sticky-session integration tests
- [x] 2.3 Add targeted selection coverage for fresh routing that proves a budget-safe account wins over a pressured one
