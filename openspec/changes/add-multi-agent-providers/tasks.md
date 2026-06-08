## 1. Provider Foundation

- [x] 1.1 Add OpenSpec proposal, design, tasks, and provider-routing delta spec.
- [x] 1.2 Add a typed backend provider registry for Codex and Gemini surfaces.
- [x] 1.3 Expose a dashboard-authenticated read-only provider metadata API.
- [x] 1.4 Add frontend schemas/API client for the provider metadata contract.
- [x] 1.5 Add lifecycle/operator metadata for Gemini API and Antigravity CLI cutover state.
- [x] 1.6 Add a backend combined provider overview API for account, quota, and request-log totals.

## 2. Gemini Runtime

- [x] 2.1 Add provider-scoped credential/account persistence for Gemini without changing Codex account rows.
- [x] 2.2 Add Gemini API request/stream adapters using provider-owned code.
- [x] 2.3 Add provider-scoped load-balancer state, quota windows, drain strategies, and preflight.
- [x] 2.4 Add Gemini dashboard accounts/settings/usage views and a backend-backed combined overview.
- [x] 2.5 Dispatch `gemini-*` models from `/v1/chat/completions` to the Gemini provider runtime.
- [x] 2.6 Expose Gemini Developer API models in `/v1/models` with provider metadata and API-key filtering.
- [x] 2.7 Add first-class Antigravity provider metadata, CLI profile accounts, and dashboard profile tab.
- [x] 2.8 Add dashboard-authenticated Antigravity `agy --print` harness execution with provider routing selection.
- [x] 2.9 Add Antigravity dashboard routing settings, quota windows, and preflight parity.
- [x] 2.10 Add manual ordered-fallback routing for Codex settings and provider-scoped routing.
- [x] 2.11 Add provider account lifecycle updates for Gemini key rotation and Antigravity profile edits.
- [x] 2.12 Add Antigravity managed-agent API-key accounts and Interactions API routing.
- [x] 2.13 Add dashboard Antigravity managed-agent run controls.
- [x] 2.14 Surface Codex routing settings in the provider dashboard alongside Gemini and Antigravity.

## 3. Verification

- [x] 3.1 Add unit tests for provider registry metadata.
- [x] 3.2 Add integration tests for provider metadata API auth and response shape.
- [x] 3.3 Add frontend schema tests.
- [x] 3.4 Run focused backend/frontend checks.
- [x] 3.5 Add mocked Antigravity harness unit and integration tests that do not launch real `agy`.
- [x] 3.6 Add integration coverage for backend provider overview aggregation.
- [x] 3.7 Add ordered-fallback unit, API, and frontend schema coverage.
- [x] 3.8 Address Codex review findings for Gemini tool calls, streaming cancellation, UTF-8 stream decoding, atomic quota settlement, and provider round-robin cursor persistence.
- [ ] 3.9 Run OpenSpec validation when the CLI is available.
  - `openspec` is not on PATH and `npx --yes openspec validate --specs` cannot determine an executable.
