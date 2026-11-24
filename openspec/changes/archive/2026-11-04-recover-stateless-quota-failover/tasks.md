## 1. Recovery
- [x] 1.1 Default pre-visible quota recovery to the unchanged stateless input.
- [x] 1.2 Identify known account-owned resources and preserve hard ownership and single-account policies.
- [x] 1.3 Expand the distinct-account budget on quota failures while retaining the old budget for other errors.
- [x] 1.4 Permit stateless turns to leave unavailable or exhausted legacy session-affinity owners without weakening explicit owner constraints.

## 2. Validation
- [x] 2.1 Cover HTTP 429 and SSE quota errors on both public Responses routes and five successive exhausted accounts.
- [x] 2.2 Cover empty text, local agent messages, tool output, unknown inline types, ciphertext, stored-resource blockers and visible-output failures.
- [x] 2.3 Exercise a real upstream continuation after injected quota failures and compare request fingerprints.
- [x] 2.4 Run focused tests, lint, type, architecture and strict OpenSpec validation on current main.
- [x] 2.5 Cover legacy affinity, account-owned blockers, disabled failover, and inline MCP and computer tool history.
