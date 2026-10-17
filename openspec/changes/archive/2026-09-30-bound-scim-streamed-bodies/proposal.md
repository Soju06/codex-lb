## Why

SCIM checks received body size only after reading the entire request. Without
a trustworthy Content-Length, its 64 KiB route limit still permits buffering
up to the global 32 MiB limit.

## What Changes

- Stop reading SCIM resource bodies when received bytes exceed 64 KiB.
- Keep the SCIM 413 response and declared-length precheck.
- Verify early refusal and valid fragmented requests.

## Impact

- Affected spec: `http-ingress-limits`.
- Affected code: SCIM request parsing.
