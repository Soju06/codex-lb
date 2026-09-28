## Why

On Windows the default asyncio loop is IOCP. It reports connect-time route loss
as Win32 errors 1231 (`ERROR_NETWORK_UNREACHABLE`) and 1232
(`ERROR_HOST_UNREACHABLE`), which CPython maps to errno `EINVAL`. The host-route
classifier matches errno only, so these failures surface as account-specific
`upstream_unavailable` and never retire the shared HTTP client, unlike
`ENETUNREACH`/`EHOSTUNREACH` on POSIX.

## What Changes

- Classify typed Windows errors 1231 and 1232 as host-route failures.
- Keep Windows peer-reset and timeout errors, including 64
  (`ERROR_NETNAME_DELETED`) and 121 (`ERROR_SEM_TIMEOUT`), account and endpoint
  attributed, matching `ECONNRESET`/`ETIMEDOUT` on POSIX.
- Keep the existing pre-dispatch replay gate and reject message-text-only
  classification.

## Impact

This changes outbound client recovery on Windows only. It adds no setting,
dependency, schema change, dashboard surface, or required setup step.
