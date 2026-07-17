## 1. Settings invariant

- [x] 1.1 Add failing settings regressions for enabled trust with empty normalized CIDRs and the disabled-trust boundary
- [x] 1.2 Add cross-field validation requiring at least one trusted-proxy CIDR whenever proxy-header trust is enabled
- [x] 1.3 Remove the narrower dashboard-mode duplicate while preserving trusted-header validation

## 2. Specification and verification

- [x] 2.1 Sync the proxy-trust configuration requirement to the main api-firewall specification
- [x] 2.2 Run focused reproduction, tests, diagnostics, formatting, and strict OpenSpec validation