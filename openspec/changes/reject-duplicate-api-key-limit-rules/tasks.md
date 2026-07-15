# Tasks: reject-duplicate-api-key-limit-rules

## 1. Validation

- [x] 1.1 Share limit-rule identity validation between API-key create and update.
- [x] 1.2 Reject duplicate identities before a create transaction writes an API key.
- [x] 1.3 Include the duplicate identity in the validation error.

## 2. Regression coverage

- [x] 2.1 Verify `POST /api/api-keys` returns the typed 400 envelope for duplicate rules and leaves no key persisted.
- [x] 2.2 Verify duplicate-rule validation identifies the conflicting rule.

## 3. Verification

- [x] 3.1 Run focused API-key tests, lint, type checks, and OpenSpec validation.
