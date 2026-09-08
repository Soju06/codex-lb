# Add portable encrypted account bundles

## Why

The existing `auth.json` import/export paths move one account at a time and expose plaintext credentials to the downloaded file. They also do not preserve codex-lb's portable account metadata. Operators need a safe way to move all or selected accounts between installations that use different at-rest encryption keys.

## What Changes

- Add a versioned account-bundle v1 format encrypted with a passphrase-derived key (scrypt + AES-256-GCM), with authenticated envelope metadata and no installation encryption material.
- Add authenticated, write-protected export, import-preflight, and import-commit APIs with bounded uploads, complete validation before writes, identity-aware skip/replace conflicts, safe audit details, and destination re-encryption.
- Add Accounts-page export and import-bundle dialogs while preserving the distinct single-account `auth.json` flows.
- Add localized English, Korean, and Chinese UI copy plus authentication and settings documentation.
- Add `CODEX_LB_ACCOUNT_BUNDLE_MAX_BYTES` (default 8 MiB). A fixed limit was rejected because deployments may need a smaller ingress budget or intentionally carry many accounts; the conservative default remains zero-config.

## Capabilities

### New Capabilities

- `account-bundles`: portable encrypted multi-account export, preflight, and import.

### Modified Capabilities

- `account-import`: the existing single-account import remains unchanged alongside the distinct bundle flow.
- `account-auth-export`: the selected-account plaintext `auth.json` export remains unchanged alongside encrypted multi-account export.
- `user-documentation`: authentication and settings documentation describes account bundles and their size setting.

## Impact

- Backend: accounts API/service/repository/schema, a focused cryptographic bundle helper, multipart routing, and settings.
- Dashboard: Accounts actions, dialogs, API schemas/hooks, query invalidation, and locales.
- Security: passphrases and plaintext exist only for the duration of each request and are never logged or retained; imports are re-encrypted with the destination installation key.
