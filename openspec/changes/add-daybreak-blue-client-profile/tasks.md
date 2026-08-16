## 1. Published client configuration

- [x] 1.1 Add canonical machine-local Codex examples that preserve the ordinary provider and define a separate Daybreak Blue provider/profile with the exact trusted-cyber header.
- [x] 1.2 Update the client setup guide with explicit profile activation, approved-identity/product-surface prerequisites, rollback guidance, and a link to the owning Responses compatibility spec.
- [x] 1.3 Document the authenticated provider-wide allowlist, unsupported route families, local model-catalog exception, and Daybreak `$imagegen` limitation in their owning specifications and client guide.

## 2. Inert seam regression

- [x] 2.1 Add a direct Responses WebSocket integration regression that loads the published TOML examples and proves Daybreak validates its inert API key and routes authorized-only before first selection while ordinary routing remains unconstrained with global API-key auth disabled.
- [x] 2.2 Confirm the existing unauthenticated-signal and empty-capable-pool fail-closed coverage remains applicable without adding external calls or credentials.
- [x] 2.3 Add authenticated HTTP Responses and compact fallback regressions that fail before routing while headerless ordinary HTTP remains unchanged.
- [x] 2.4 Add and run an opt-in installed-Codex loopback regression for real profile resolution, environment-key handling, first WebSocket header emission, and retained HTTP-fallback headers.
- [x] 2.5 Add authenticated fail-closed regressions for provider-bound control, admission, warmup, files, transcription, chat, and Images HTTP routes while preserving local model-catalog initialization and the separate WHAM namespace.
- [x] 2.6 Add a non-Responses WebSocket regression that proves the carrier is authenticated and denied before Live owner lookup or upstream connection while headerless behavior remains unchanged.
- [x] 2.7 Inventory every runtime-registered proxy route, assign one explicit carrier policy, and cover reset-credit, local usage, signed internal-bridge, and middleware-alias seams so new unclassified provider ingress fails the regression.

## 3. Verification

- [x] 3.1 Rerun scoped OpenSpec validation, focused capability-routing regressions, affected lint/format checks, documentation build, and `git diff --check` after security-review remediation.
- [x] 3.2 Document the remediated provider-wide transport boundary, Images fail-closed behavior, and `$imagegen` limitation in the change artifacts and client guide.
- [x] 3.3 Inspect the final committed diff with one independent Sensitive review and address every actionable in-scope finding before publication.
- [x] 3.4 Keep capability-header authentication on the existing `validate_proxy_api_key` dependency identity so FastAPI overrides and auth-first upload tests continue to apply.
- [x] 3.5 Deny capability-bearing typed JSON routes before FastAPI decodes the request body.
