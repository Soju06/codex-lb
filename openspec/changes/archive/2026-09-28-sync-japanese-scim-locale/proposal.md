## Why

After SCIM account management landed in main, PR #2118's Japanese bundle lacks 23 English keys and fails the existing locale integrity tests. The automatic account management card and organisation summaries need Japanese copy before the locale PR can merge.

## What Changes

- Merge current `origin/main` into the existing Japanese locale branch.
- Translate the 20 automatic-account-management strings, the missing-credential error, and both automatic-management summaries while preserving interpolation variables.
- Verify locale parity and the integrated frontend, and record the SCIM coverage in the frontend capability.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Extend Japanese feature-surface coverage with the automatic account management card, credential dialog, and organisation summaries.

## Impact

- `frontend/src/i18n/locales/ja.json` and the existing frontend-architecture OpenSpec capability.
- No new application logic, dependencies, settings, or API contracts.
- Addresses [maintainer comment 5866459612](https://github.com/Soju06/codex-lb/pull/2118#issuecomment-5866459612); that comment explicitly waives a new screenshot set for this sync.
