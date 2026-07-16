## 1. Trusted Forwarded Resolution

- [x] 1.1 Parse complete RFC 7239 `Forwarded` chains into validated IP hops and reject ambiguous or malformed elements
- [x] 1.2 Resolve parsed `Forwarded` and `X-Forwarded-For` hops through one right-to-left trusted-proxy algorithm
- [x] 1.3 Consume every repeated `Forwarded` and `X-Forwarded-For` field as one ordered chain while retaining plain mapping support
- [x] 1.4 Remove the firewall-local resolver and migrate HTTP firewall, WebSocket firewall, and trusted-header sanitizer callers to shared locality helpers

## 2. Regression Evidence

- [x] 2.1 Add focused resolver tests for preseeded chains, trusted chains, malformed elements, quoted delimiters, and IP nodes with ports
- [x] 2.2 Add an integration regression proving a preseeded loopback `Forwarded` value cannot authorize remote dashboard bootstrap
- [x] 2.3 Add an ASGI-level regression for a proxy-appended second `Forwarded` field and duplicate `X-Forwarded-For` coverage
- [x] 2.4 Update firewall resolver regressions for duplicate fields and fail-closed missing or malformed trusted-proxy chains
- [x] 2.5 Run the original exploit reproduction, focused tests, diagnostics, and strict OpenSpec validation
