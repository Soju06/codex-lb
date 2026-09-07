## Verification

- Limit-window and reset-scheduler unit tests: 34 passed.
- API-key service and repository unit tests: 97 passed.
- API-key integration tests selected by `daily_limit or reset_expired_limits_background`: 7 passed, 99 deselected. Coverage includes create/reset API responses around local midnight, lazy expiry via `/v1/usage`, background expiry, and alignment preserving usage and other windows.
- Ruff lint and format checks passed for all five changed Python files; `git diff --check` passed.
- Strict validation passed for this change and the synced `api-keys` main spec. The renamed midnight scenario was synced with the full requirement before archive so the obsolete UTC-midnight title is not retained.
- Repository-wide `openspec validate --specs` reported 58 passed and one existing failure: `model-source-routing` lacks a Purpose section. That file is byte-for-byte unchanged from HEAD. Repository-wide strict validation reported 36 passed and 23 failed, including existing placeholder-Purpose warnings. These unrelated spec issues are outside this change.
- Stable operational context is recorded in `openspec/specs/api-keys/context.md`.

## Rollout

This change updates source only. Production deployment has not been performed.
