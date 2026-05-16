## 1. Upstream transport control

- [x] 1.1 Add a dashboard setting and persistence path for upstream streaming transport selection
- [x] 1.2 Route streaming upstream traffic through HTTP or native WebSockets based on the resolved setting

## 2. Codex interoperability

- [x] 2.1 Normalize `service_tier: "fast"` to upstream-compatible `"priority"`
- [x] 2.2 Preserve requested-versus-actual service-tier observability without changing billable streaming request-log tiers

## 3. UI and documentation

- [x] 3.1 Add the upstream stream transport control to the dashboard routing settings UI
- [x] 3.2 Document the dashboard control and note the experimental Codex-side websocket feature flags

## 4. Verification

- [x] 4.1 Add regression coverage for request normalization, transport override resolution, and settings persistence
- [x] 4.2 Verify backend tests, frontend tests, frontend build, and OpenSpec validation
