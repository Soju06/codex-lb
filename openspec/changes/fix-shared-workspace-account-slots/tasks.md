## 1. Account Identity

- [x] 1.1 Scope ChatGPT identity reconciliation by email as well as workspace identity.
- [x] 1.2 Persist workspace-less OAuth logins through the account-slot path so different emails do not merge.

## 2. Tests

- [x] 2.1 Add regression coverage for two emails sharing the same `chatgpt_account_id` and `workspace_id`.
- [x] 2.2 Add regression coverage for two workspace-less emails sharing the same `chatgpt_account_id`.
- [x] 2.3 Add regression coverage for ChatGPT account id workspace display fallback.

## 3. Validation

- [ ] 3.1 Run focused account repository/API tests.
- [ ] 3.2 Run OpenSpec validation.
