## Context

`is_local_request()` gates first-run dashboard setup and unauthenticated protected proxy access. In trusted-proxy mode it currently resolves the client through configured proxy CIDRs, but its final loopback decision separately treats any forwarded client-IP header as provenance. If the socket peer is loopback but outside the configured trusted CIDRs, resolution correctly ignores the header and returns the socket address; the raw header-presence check then incorrectly turns that loopback address into a local identity.

PR #339 introduced the provenance requirement to keep remote traffic behind loopback proxies from becoming local. PR #399 deliberately kept the later unauthenticated-proxy escape hatch bound to the raw socket peer and left dashboard/bootstrap auth protected. The correction must preserve both decisions.

## Goals / Non-Goals

**Goals:**

- Require a configured trusted socket peer and a successfully resolved forwarded client identity before trusted-proxy mode can classify loopback identity as local.
- Preserve direct-local behavior when proxy-header trust is disabled.
- Preserve explicit raw-socket `proxy_unauthenticated_client_cidrs` behavior.
- Cover the externally failing dashboard bootstrap path as well as the shared locality predicate.

**Non-Goals:**

- Change forwarded-chain parsing or header precedence.
- Add configuration validation or new settings.
- Broaden local access for Docker or other non-loopback bridge peers.

## Decisions

### Bind trusted-proxy locality to both socket trust and resolved identity

`is_local_request()` will compute the configured proxy networks once, resolve the client through the existing shared resolver, and retain the raw socket peer for provenance. In trusted-proxy mode, a resolved loopback address is local only when the raw socket peer belongs to those networks and the Host header is local. A trusted peer without a usable forwarded identity already resolves to no client and remains remote.

Alternative: keep the raw header-presence check and additionally test the socket CIDR. Rejected because successful resolution is the stronger evidence: it also excludes missing and malformed values without duplicating resolver policy.

### Inspect every direct-mode forwarded hint field

When proxy-header trust is disabled, the resolver intentionally returns the socket peer and the locality predicate separately rejects requests carrying a non-empty forwarded client-IP hint. For Starlette `Headers`, that scan must use all field values because `get()` exposes only the first repeated field. Plain `Mapping[str, str]` inputs retain their single-value behavior.

Alternative: reject any forwarded header name regardless of value. Rejected to preserve established direct-local behavior for empty header fields while still failing closed if any repeated value is non-empty.

### Keep the explicit proxy escape hatch independent

No change is made to `proxy_unauthenticated_client_cidrs`. Its existing raw-socket match remains the only way an otherwise non-local protected proxy request may proceed without API-key auth.

Alternative: treat the escape-hatch CIDRs as locality. Rejected because PR #399 intentionally scoped them to proxy traffic and excluded dashboard/bootstrap authorization.

## Risks / Trade-offs

- [A proxy's actual socket address is absent from trusted CIDRs] → Requests remain remote, matching documented configuration and fail-closed behavior; operators must configure the observed proxy CIDR.
- [Legitimate forwarded loopback identity regresses] → Preserve and test the case where the socket peer is configured as trusted and the resolved forwarded client is loopback.
- [Shared locality affects dashboard and proxy auth] → Exercise both the unit predicate and an end-to-end password-bootstrap denial; retain the raw-socket proxy allowlist tests.

## Migration Plan

No data or configuration migration. Deploy as a fail-closed security correction. Roll back by reverting the implementation commit if a documented trusted-proxy setup regresses.