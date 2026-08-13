## 1. Published client configuration

- [x] 1.1 Add canonical machine-local Codex examples that preserve the ordinary provider and define a separate Daybreak Blue provider/profile with the exact trusted-cyber header.
- [x] 1.2 Update the client setup guide with explicit profile activation, approved-identity/product-surface prerequisites, rollback guidance, and a link to the owning Responses compatibility spec.

## 2. Inert seam regression

- [x] 2.1 Add a direct Responses WebSocket integration regression that loads the published TOML examples and proves Daybreak validates its inert API key and routes authorized-only before first selection while ordinary routing remains unconstrained with global API-key auth disabled.
- [x] 2.2 Confirm the existing unauthenticated-signal and empty-capable-pool fail-closed coverage remains applicable without adding external calls or credentials.
- [x] 2.3 Add authenticated HTTP Responses and compact fallback regressions that fail before routing while headerless ordinary HTTP remains unchanged.
- [x] 2.4 Add and run an opt-in installed-Codex loopback regression for real profile resolution, environment-key handling, first WebSocket header emission, and retained HTTP-fallback headers.

## 3. Verification

- [x] 3.1 Run scoped OpenSpec validation, the focused capability-routing unit/integration tests, affected lint/format checks, documentation build, and `git diff --check`.
- [ ] 3.2 Inspect the final committed diff with one independent Sensitive review and address every actionable in-scope finding before publication.
