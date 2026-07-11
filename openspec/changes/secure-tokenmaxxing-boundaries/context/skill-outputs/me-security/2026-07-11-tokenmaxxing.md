# Security review: Tokenmaxxing

Verdict: blocked for deployment until origin JWT verification, retention, WARP
client authentication, and single-writer cutover controls are implemented and
proven. This review was read-only.

## Findings

| Severity | Finding / exploit condition | Asset and evidence | Fix route | Fingerprint |
|---|---|---|---|---|
| High | A process reaching a trusted proxy CIDR can supply the configured identity header; Codex-LB does not validate Cloudflare JWT claims. | Full dashboard/OAuth/API-key control; `app/core/auth/dashboard_mode.py:94-111` | Verify Access JWT via JWKS with exact issuer/audience/expiry/domain and derive actor from its email claim. | `TM-SEC-001/cloudflare-jwt-origin` |
| High | Compromise of any `@onda.lol` mailbox permits OTP login and full destructive administration. | Four OAuth grants, routing, firewall, and unrestricted key creation; explicit all-admin decision. | Accepted owner risk: require short sessions, offboarding SLA, mutation audit alerts, and rehearsed rollback. | `TM-SEC-002/domain-wide-admin` |
| High | No macOS client, WARP policy, capacity endpoint contract, or denied native path exists. | Distributed client and capacity boundary. | Require both enrolled WARP identity and per-device bearer key; prove enrolled success and unenrolled/non-domain/keyless denial; embed no service token. | `TM-SEC-003/warp-native-auth` |
| High | Unique uncapped bearer keys can still be copied/shared and are not bound to Access identity. | Pooled quota and attribution; API keys are hash-validated bearer credentials. | Controlled issuance ledger, identity/device naming, one-time Keychain delivery, revocation/offboarding, audit alerts, and emergency pool limiter. | `TM-SEC-004/bearer-attribution-abuse` |
| High | Identifiable request logs and backups currently have no 30-day expiry. | API-key/session/IP/user-agent/account/model/error history; request-log model and repository. | Idempotent hard-delete scheduler, no reconstructable long-term rollup, age-bounded backup deletion, and restored-backup proof. | `TM-SEC-005/request-retention` |
| Medium | Conversation archive and payload logging toggles could persist prompts despite safe defaults. | Prompt/response content; `app/core/config/settings.py` and `app/core/conversation_archive.py`. | Force archive/payload flags off in deployment, drift-check them, exclude archives from backups, and prove canary prompt absence. | `TM-SEC-006/payload-persistence-toggle` |
| Medium | Prodex/Codex refreshers are not yet fenced. | All four long-lived OAuth grants. | Zero-writer proof, sequential canary import, two request/refresh cycles, then remaining accounts; restore only latest grants to one writer. | `TM-SEC-007/oauth-single-writer` |
| Medium | Tunnel, listener, firewall, and direct-IP isolation are only planned. | Dashboard and proxy API. | Loopback-only listeners, named Tunnel, provider firewall, reboot listener inventory, direct-IP/Host/header spoof denial. | `TM-SEC-008/origin-exposure` |

## Load-bearing clears

- Trusted-header mode refuses incomplete proxy/CIDR configuration.
- Remote proxy traffic is not unauthenticated merely because API-key auth is
  disabled unless its socket peer is explicitly allowlisted.
- API-key plaintext is random, hash-stored, shown once, and revocable.
- Conversation archiving and payload logging default off, but deployment must
  pin and prove those defaults.
- Focused auth/settings tests passed: 26 tests.

## No-fire list

- No payment execution or external tenant boundary is in scope.
- Never preserve HARs, cookies, Access JWTs, OAuth grants, API-key plaintext,
  Apple credentials, raw DB rows, or payload-bearing logs as evidence.
- Any secret in terminal output, git, image, app bundle, screenshots, CI, or
  logs triggers stop and rotation.

## Evidence limit

Source and focused unit-test evidence only. Cloudflare, Hetzner, WARP, browser,
backup, OAuth cutover, and real Mac behavior remain unverified.
