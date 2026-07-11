# Technical approach review: Tokenmaxxing successor

Verdict: revise before implementation. The core direction is viable, and the
existing `/api/fleet/summary` endpoint is the correct capacity seam, but the
contract requires the changes below before deployment.

Scope posture: hold the chosen product; reduce implementation to additive
Codex-LB controls plus a separately staged macOS release.

## Findings

| Severity | Finding | Evidence | Required revision / flip evidence | Fingerprint |
|---|---|---|---|---|
| High | Trusted-header mode does not validate Cloudflare's JWT, issuer, audience, expiry, or email. A local/tunnel-side process could spoof an administrator. | `app/core/auth/dashboard_mode.py:94-111`; `app/core/middleware/dashboard_auth_proxy.py:27-43` | Verify `Cf-Access-Jwt-Assertion` with remote JWKS, exact issuer/audience, expiry, and `@onda.lol` email; derive the actor from the verified claim. Prove forged, expired, wrong-audience/issuer/domain, missing, mismatched, and live valid paths. | `cf-access-header-without-jwt` |
| High | The active plan says Prodex stays live until post-import smoke, while the contract requires zero old writers before import. | active plan decision log; successor AC6 | Checkpoint/export current grants, stop all writers, import into isolated Codex-LB, bounded-smoke, then promote or restore the latest grants to exactly one Prodex writer. Prove zero writers before import and exactly one afterward. | `dual-writer-cutover-order` |
| High | Thirty-day deletion lacks exact primary/backup mechanics and idempotent failure behavior. | `app/db/models.py:179-249`; `app/db/backup.py`; no request-log retention scheduler | V1 keeps no long-term identifiable request aggregate: hard-delete request logs at a UTC 30-day boundary, retry idempotently, expire every backup containing older rows, and preserve only quota history that cannot reconstruct employee requests. | `30d-retention-without-backup-policy` |
| High | Capacity denominator, stale/unavailable behavior, snapshot time, rounding, and Spark semantics are undefined. | `app/modules/fleet/api.py:54-78`; `app/modules/fleet/schemas.py:10-35` | Add an additive server-authoritative fleet summary: generated time, included/excluded IDs/status, five-hour/weekly used percentage, stale flag/reason, per-account values, and Spark. Active missing/stale data makes the aggregate stale instead of silently healthy. Prove a truth table and live same-snapshot comparison. | `undefined-utilization-denominator` |
| Medium | WARP identity does not replace the bearer API key required by `/api/fleet/summary`. | fleet route and API-key dependency | Manual v1 onboarding: create one per-user/device key, copy once into Keychain/config, require both WARP Access and bearer key, and prove each missing/revoked path fails. | `warp-is-not-api-key-provisioning` |
| High | Whole-host Access requires every Codex API client to use WARP; path bypass would create a different public boundary. | planned single hostname and gateway paths | Keep whole-host Access and require WARP for every employee Codex client in v1. Enumerate and prove HTTP/SSE/WebSocket paths on an enrolled Mac and deny a control device. | `access-dashboard-vs-codex-api-path` |
| Medium | A parallel capacity service would duplicate existing ownership. | `/api/fleet/summary` and integration tests | Extend the existing endpoint only with versioned/additive fields; compute utilization server-side. | `parallel-capacity-service-unnecessary` |
| Medium | Signed/notarized app release cannot pass without Apple credentials and a real managed Mac. | successor AC13 | Stage web/gateway independently. macOS source/dev proof may advance; employee DMG remains blocked until clean-device signing/notarization proof. Defer automatic updates from v1 unless feed/signing ownership is verified. | `apple-release-prerequisite` |

## Smallest viable sequence

1. JWT-validating Access boundary and private origin.
2. Zero-writer four-account cutover.
3. Additive fleet-summary utilization/staleness/Spark fields.
4. Thirty-day request-log and backup expiry without long-term employee-level rollups.
5. Menu app with WARP plus manually provisioned per-device bearer key.
6. Separately gated signed/notarized DMG release; updater only after its own proof.

## Next route

Revise the successor contract without changing its product decisions, then
continue `me-build-and-prove`.
