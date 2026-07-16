## Why

Trusted-proxy client resolution validates `X-Forwarded-For` from right to left, but accepts the first `for=` value in an RFC 7239 `Forwarded` header. A client can therefore pre-seed an appended `Forwarded` chain with a loopback address and be misclassified as local, bypassing remote-only dashboard bootstrap and proxy-auth protections.

## What Changes

- Parse every RFC 7239 `Forwarded` hop and resolve the effective client from right to left using the configured trusted-proxy CIDRs.
- Join every repeated `Forwarded` or `X-Forwarded-For` field value in arrival order before parsing the effective chain.
- Fail closed when any hop is missing, obfuscated, malformed, or otherwise cannot establish a complete IP chain.
- Reject repeated singleton client-IP fields instead of trusting whichever value the HTTP runtime exposes first.
- Preserve support for valid IPv4 and bracketed IPv6 `for=` values, including optional ports.
- Add regression coverage for attacker-preseeded chains, trusted multi-proxy chains, malformed chains, and IPv6 values.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `admin-auth`: Require trusted-proxy client identity and local-request classification to resist spoofed or malformed appended `Forwarded` chains.
- `api-firewall`: Require firewall client resolution to use the same complete, duplicate-field-safe trusted-proxy chain rules.

## Impact

- Affected code: shared request-locality resolution, API firewall middleware, trusted-header sanitization, WebSocket firewall resolution, and focused tests.
- Affected behavior: firewall allowlist identity, dashboard bootstrap, unauthenticated proxy access checks, session lifetime locality, and request metadata that rely on trusted client IP resolution.
- Dependencies and persistent schema: unchanged.
