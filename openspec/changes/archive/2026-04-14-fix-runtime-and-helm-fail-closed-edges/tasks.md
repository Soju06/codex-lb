## 1. Specs

- [x] 1.1 Add runtime proxy requirements for half-open websocket probes, request cleanup, account revalidation, and bridge drain reuse.
- [x] 1.2 Add Helm requirements for external database resolution, pre-pod migration ordering, and fail-closed ingress policy defaults.
- [x] 1.3 Validate OpenSpec changes.

## 2. Tests

- [x] 2.1 Add regression coverage for websocket circuit-breaker probe retention and rejected aiohttp request cleanup.
- [x] 2.2 Add regression coverage for concurrent account-status revalidation and bridge session reuse during drain.
- [x] 2.3 Add Helm rendering regression coverage for external DB secret/url wiring, install hook timing, and ingress namespace defaults.

## 3. Implementation

- [x] 3.1 Patch proxy websocket and request context handling to keep half-open probes single-flight across the full lifecycle.
- [x] 3.2 Patch load balancer and bridge admission logic to fail closed on concurrent state changes without breaking live-session continuity.
- [x] 3.3 Patch Helm helpers/templates to resolve external DB configuration safely, run fresh-install migrations before pods, and keep ingress deny-by-default.
