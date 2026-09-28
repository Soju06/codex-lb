## Why

PR #2118's Japanese bundle has drifted from main: the merged tree has 78 missing keys and 53 obsolete keys, causing its locale integrity tests to fail. New company sign-in, approval, and account-management screens need Japanese copy before the PR can merge.

## What Changes

- Merge current main into the existing Japanese locale branch.
- Translate the new OIDC connection, company sign-in, pending-account, provider confirmation, and account rename strings.
- Remove Japanese keys deleted from English, including the reverted subscription overflow feature.
- Verify key/interpolation parity, the full frontend suite, and representative browser screens with English/Japanese captures.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Extend the localized feature-surface requirement to Auth, Access, and Organisation, including the OIDC wizard and provider sign-in states.

## Impact

The repair changes `frontend/src/i18n/locales/ja.json`, browser verification, and frontend OpenSpec documentation. It adds no application logic, dependencies, settings, or database migrations beyond the requested main synchronization.
