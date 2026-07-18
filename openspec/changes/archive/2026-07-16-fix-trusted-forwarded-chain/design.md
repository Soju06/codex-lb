## Context

`resolve_connection_client_ip()` is the shared trust boundary for firewall decisions, unauthenticated proxy access, dashboard bootstrap, session lifetime locality, and request metadata. Its `X-Forwarded-For` path already walks an appended chain from the trusted socket peer toward the client, but its RFC 7239 `Forwarded` fallback returns the first `for=` parameter. Because compliant proxies append elements, the first element remains attacker-controlled when a client supplies the initial header.

`api_firewall.py` also defines a second `resolve_connection_client_ip()` and CIDR parser. That local function shadows the shared resolver for HTTP middleware and is imported by the WebSocket firewall path, so fixing only `request_locality.py` would leave the firewall trust boundary vulnerable and preserve semantic drift.

The resolver must remain fail-closed: a partial or ambiguous chain cannot establish a trustworthy client identity. No external parser dependency is justified for the small IP-only subset consumed here.

## Goals / Non-Goals

**Goals:**

- Apply the same right-to-left trusted-hop algorithm to `Forwarded` and `X-Forwarded-For`.
- Parse comma-separated elements and semicolon-separated parameters without treating delimiters inside quoted strings as structure.
- Accept valid IP node identifiers used by deployed proxies: IPv4, bracketed IPv6, and optional numeric ports.
- Reject incomplete, duplicate, obfuscated, unknown, malformed, or non-IP `for=` hops as one invalid chain.
- Remove the firewall-local resolver and migrate firewall, WebSocket, and trusted-header sanitizer callers to the shared implementation.
- Preserve existing header precedence and trusted-socket gating.

**Non-Goals:**

- Trust `by=`, `host=`, or `proto=` parameters.
- Resolve obfuscated node identifiers or hostnames.
- Change trusted-proxy configuration, firewall policy, or header precedence.
- Add a general-purpose RFC 7239 library.

## Decisions

### Parse the complete `Forwarded` chain before resolving it

Each comma-delimited forwarded element must contain exactly one valid `for=` parameter. A quote-aware splitter rejects unmatched quotes and dangling escapes; the node parser then extracts an IP and discards an optional valid port. Any invalid element invalidates the entire header.

Alternative: skip malformed elements and use the remaining IPs. Rejected because deleting an attacker-controlled or intermediary hop changes chain ownership and can move a spoofed value into the trusted position.

### Resolve from the socket inward

Starting with the socket peer, consume parsed hops right to left. Advance past a hop only while the current peer belongs to a configured trusted CIDR; return the first hop reached from an untrusted peer. This matches append semantics and the existing `X-Forwarded-For` trust model.

Alternative: select the leftmost or rightmost header value unconditionally. Rejected because neither distinguishes attacker-supplied values from values appended by trusted intermediaries.

### Preserve every repeated chain-header field

When the runtime header object exposes `getlist()`, join all values in arrival order with commas before parsing. Retain `Mapping[str, str]` support by treating its single value as the complete chain. Apply this to both `Forwarded` and `X-Forwarded-For` so the shared trust model cannot diverge at the field-normalization boundary.

Alternative: use `headers.get()` and assume intermediaries combine repeated fields. Rejected because RFC 7239 explicitly permits multiple `Forwarded` fields and Starlette returns only the first duplicate from `get()`.

### Reject repeated singleton identity fields

Unlike `Forwarded` and `X-Forwarded-For`, `X-Real-IP`, `True-Client-IP`, and `CF-Connecting-IP` each assert one client identity rather than an ordered chain. When the runtime exposes repeated fields, the shared resolver rejects more than one value instead of accepting Starlette's first-value `get()` result.

Singleton repetition is validated before header precedence is applied. A valid higher-priority `X-Forwarded-For` chain therefore cannot hide an ambiguous repeated singleton field from general trusted-proxy resolution; firewall callers remain unaffected because their explicit header policy excludes singleton fields.

Alternative: accept the first or last singleton field. Rejected because a client and proxy can each supply one field, and field order alone does not prove which party owns either value.

### Share only the chain trust algorithm

Keep format-specific parsing separate, then pass both IP lists to a small common right-to-left resolver. This removes the semantic drift without introducing a general header abstraction.

Alternative: translate `Forwarded` into an `X-Forwarded-For` string. Rejected because serializing parsed data back into another wire format adds ambiguity and avoidable work.

### Consolidate every firewall caller on the shared resolver

Delete the firewall-local client-chain, CIDR, and trusted-source implementations. HTTP firewall middleware and the WebSocket firewall path import the shared resolver and CIDR parser; trusted-header sanitization imports the shared trusted-source predicate.

The shared resolver accepts an explicit allowed-header policy. Firewall callers pass only `X-Forwarded-For` and `Forwarded`; general request-locality callers retain their existing singleton `X-Real-IP`, `True-Client-IP`, and `CF-Connecting-IP` fallbacks. This keeps consolidation from silently broadening the firewall trust contract.

Alternative: patch both implementations in parallel. Rejected because duplicated security logic already drifted in header formats, malformed-chain behavior, and repeated-field handling.

## Risks / Trade-offs

- [Previously accepted malformed or partial `Forwarded` values stop resolving] → This is intentional fail-closed behavior at an authentication boundary; valid single-hop values remain supported.
- [Some proxies emit nonstandard hostnames or obfuscated identifiers] → Continue rejecting them because configured trust is IP/CIDR-based and cannot authenticate those identifiers.
- [Parser edge cases around quoting] → Cover escaped quoted strings, unmatched quotes, duplicate parameters, token and quoted-string character syntax, mandatory quoting for IPv6 and port-bearing nodes, numeric port bounds, and bracketed address-family validation with focused tests.
- [Shared resolver behavior affects several callers] → Preserve socket trust gating and header precedence, then exercise dashboard bootstrap and firewall-facing ASGI headers plus the existing firewall resolver suite.

## Migration Plan

Deploy as a code-only security correction; no data or configuration migration is required. Operators with valid proxy-generated `Forwarded` chains retain behavior. Rollback is the branch commit reversal.

## Open Questions

None.
