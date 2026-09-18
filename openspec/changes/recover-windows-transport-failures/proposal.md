## Why

Windows transport failures 64 (`ERROR_NETNAME_DELETED`) and 121
(`ERROR_SEM_TIMEOUT`) currently bypass typed local transport recovery. The proxy
surfaces them as account-specific `upstream_unavailable` failures, even though a
fresh shared HTTP client should serve later requests.

## What Changes

- Classify typed Windows errors 64 and 121 as local transport failures.
- Retire the failed shared HTTP client without replaying ambiguous requests.
- Permit same-account retry only when existing connector provenance proves the
  request failed before dispatch.
- Keep account health neutral and reject message-text-only classification.

## Impact

This changes outbound client recovery only. It adds no setting, dependency,
schema change, dashboard surface, or required setup step.
