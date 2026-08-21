## 1. Repair Path

- [x] 1.1 Remap `20260814_020000_merge_identity_and_warmup_heads` to the current pre-repair Alembic revision
- [x] 1.2 Add a forward-only repair migration that replays the missing current-main migrations and removes `idx_accounts_chatgpt_account_id` plus `quota_planner_decisions.lease_expires_at` when present

## 2. Validation

- [x] 2.1 Add a regression test for the representative SQLite drift shape stamped at the retired merge head
- [x] 2.2 Validate OpenSpec and the focused migration tests
