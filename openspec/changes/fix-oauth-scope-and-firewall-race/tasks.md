## 1. OpenSpec update

- [x] 1.1 Document OAuth scope-key consistency requirement for dashboard auth flows
- [x] 1.2 Document firewall duplicate-write conflict handling requirement

## 2. Implementation

- [x] 2.1 Align OAuth start scope-key derivation with request fallback strategy
- [x] 2.2 Make firewall allowlist insert path return conflict on duplicate insert races

## 3. Validation

- [x] 3.1 Add/update regression tests for OAuth cookie-less flow and firewall duplicate conflict mapping
- [ ] 3.2 Run `openspec validate --specs` (blocked in this environment: `openspec` CLI not installed)
- [x] 3.3 Run lint and test suite (`ruff`, `ty`, `pytest`)
