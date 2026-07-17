## 1. Contract

- [x] 1.1 Define the standalone Codex search forwarding requirement under
  `responses-api-compat`.
- [x] 1.2 Record authentication, account-routing, wire-fidelity, preflight, and
  response header constraints.

## 2. Implementation

- [x] 2.1 Register the GET/POST Codex search routes through the existing control
  proxy and keep browser OPTIONS preflight local.
- [x] 2.2 Add unit route-contract and integration forwarding regressions.

## 3. Verification

- [x] 3.1 Run strict validation for `forward-codex-alpha-search` and all specs.
- [x] 3.2 Run focused lint, type checking, unit, and integration tests.
- [x] 3.3 Run the full local CI gate and review the final diff against current
  upstream `main`; record any unrelated gate failure in the PR test plan.
