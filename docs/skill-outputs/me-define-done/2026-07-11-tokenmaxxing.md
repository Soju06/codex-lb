# Execution Contract: Tokenmaxxing Codex-LB service and macOS capacity client

## Run Context

| Field | Value |
|---|---|
| Context / Receipt Ref | `docs/skill-outputs/me-grill/2026-07-11-tokenmaxxing.md`; predecessor `docs/skill-outputs/me-define-done/2026-07-10-codex-lb-hetzner.md` |
| Session | interactive |
| Status | complete |
| Authority Used | planning-artifact writes only; no product code, cloud mutation, credential import, deployment, or publication |

## Source Context

| Field | Value |
|---|---|
| Chosen Direction | Deploy the unchanged Codex-LB dashboard at `tokenmaxxing.onda.systems`, make Codex-LB the sole four-account gateway, and add a signed WARP-authenticated macOS menu-bar capacity client. |
| Source Request / Artifact | Current user decisions; `docs/skill-outputs/me-grill/2026-07-11-tokenmaxxing.md`; existing Hetzner contract and technical review. |
| Actor / User Impact | Every `@onda.lol` user can administer and consume the shared pool, view the web dashboard, and see normalized five-hour utilization in the macOS menu bar. |
| Constraints | Original dashboard appearance; one Access layer with Google or OTP; WARP for native auth; per-user/device gateway keys; no prompt bodies; 30-day request-metadata retention; one OAuth refresh writer; no public host ports. |
| Upstream Artifact Check | Used `docs/skill-outputs/me-review-technical-approach/2026-07-10-codex-lb-hetzner.md`; its Tailscale-only ingress decision is superseded by Access plus Tunnel, while its single-writer, immutable-artifact, persistence, and rollback findings remain binding. |
| Live Verification Limits | Cloudflare resources, Apple signing credentials, four-account import, and deployed runtime are not verified in this contract-writing session. |

## Selected Principles

| Principle | Reason | Action |
|---|---|---|
| `least-privilege-access.md` | OAuth grants, Access identity, WARP, API keys, and Apple credentials cross systems. | Require loopback origin, tunnel-only ingress, per-user/device gateway keys, Keychain storage, and secret-presence proof without values. |
| `trust-boundaries.md` | Browser, WARP client, tunnel, origin, and gateway are distinct trust zones. | Validate Access JWT/header provenance and audience; reject direct/spoofed origin requests. |
| `proxy-secrets-and-auth-artifacts.md` | The cutover handles long-lived refresh grants and signing material. | Use sealed/path-scoped stores, redact all evidence, and prohibit shared embedded service tokens. |
| `one-source-of-truth.md` | Prodex and Codex-LB cannot both own rotated grants or capacity truth. | Fence Prodex before import and define Codex-LB as the sole writer and capacity source. |
| `right-to-erasure.md` | Employee request metadata otherwise persists indefinitely. | Enforce and prove 30-day deletion plus backup expiry; retain no long-term employee-level request aggregate in v1. |
| `user-visible-proof.md` | Web login, dashboard, DMG, menu value, and stale/error states are user-visible. | Require real browser and signed-app evidence, not source inspection alone. |
| `probe-end-to-end.md` | Requests cross device, Access/WARP, Tunnel, Codex-LB, accounts, and OpenAI. | Prove the full paths with real identities and bounded canary requests. |
| `separate-decision-from-effect.md` | Cloud, DNS, account import, and session cutover are external writes. | Require dry-run packets and exact target review before every effect. |
| `optimize-for-reversibility.md` | Refresh-token rotation and native-client distribution are difficult to undo. | Preserve latest-grant rollback, canary promotion, pinned artifacts, and update rollback. |
| `visible-failure-modes.md` | Stale quota or failed auth could look like healthy low usage. | Surface last refresh, stale state, auth state, unavailable accounts, and operational logs/metrics. |

## Rejected Principles

| Principle | Reason |
|---|---|
| `exhaust-design-space.md` | The grill selected the existing dashboard, Cloudflare, WARP, and native client direction; reopening alternatives would undo settled scope. |
| `redesign-from-first-principles.md` | Visual redesign and dashboard RBAC were explicitly excluded. |

## Outcome

At the exact reviewed revisions, `tokenmaxxing.onda.systems` serves the original
Codex-LB dashboard only to `@onda.lol` identities through Google or OTP;
Codex-LB is the only refresh writer and gateway for four healthy accounts; each
employee/device uses an attributable gateway key; and a signed/notarized macOS
menu-bar app shows accurate normalized five-hour utilization with weekly and
per-account detail. Request metadata older than 30 days is deleted, prompts and
responses are never persisted, and every external surface has a tested rollback.

## Scope

### In Scope

- Finish and independently review the predecessor repo-operability criteria
  required to build an immutable Codex-LB artifact.
- Dedicated single-node Codex-LB deployment with persistent encrypted state,
  loopback listeners, Cloudflare Tunnel, Access, DNS, and WARP integration.
- Google and OTP alternative login methods restricted to `@onda.lol`.
- Full existing dashboard administration for every admitted user, without
  visual rebranding or new RBAC.
- Single-writer migration of the four existing Prodex profiles and canary-first
  Codex client cutover.
- Per-person/device Codex-LB API keys without initial hard usage caps.
- A separate private macOS menu-bar app, signed/notarized DMG, automatic update
  path, Keychain use, normalized capacity calculation, and dashboard deep link.
- Thirty-day request-level metadata retention, backup expiry, and no long-term
  employee-level request aggregate in v1.

### Out Of Scope

- A new web UI, dashboard redesign, Tokenmaxxing logo inside Codex-LB, read-only
  roles, or restrictions that contradict the explicit all-admin decision.
- Public origin ports, service-token credentials embedded in the app,
  concurrent Prodex refresh writing, shared company gateway keys, or per-user
  hard usage caps in v1.
- Prompt/response-body storage, employee notifications, mobile/Windows/Linux
  apps, multi-region/multi-replica deployment, or PostgreSQL migration.

### Stop / Ask-First Surfaces

| Surface | Boundary | Required Decision |
|---|---|---|
| Cloudflare DNS/Access/Tunnel/WARP mutation | External security boundary | Exact account/zone/app target, dry-run diff, rollback, and current user authority. |
| Hetzner provision/deploy | Billable external infrastructure | Exact server type/region/price, immutable image identity, firewall plan, and deployment grant. |
| OAuth credential import | Rotating long-lived account grants | All old writers stopped, checkpoints complete, sealed destination ready, and tested latest-grant rollback. |
| Apple signing/notarization | External release and private keys | Credential presence, bundle/team identifiers, notarization path, update-signing design, and release grant. |
| Employee release | Client-boundary distribution | Signed artifact, update/rollback channel, privacy text, and fresh independent review. |

### Likely Touched Surfaces

| Surface | Expected Change | Risk |
|---|---|---|
| Codex-LB repo and OpenSpec | Deployment/auth/retention adapters and tests; no visual redesign. | Upstream merge burden and auth regression. |
| Dedicated host | Container, persistent data/key pair, backup, cloudflared, firewall, observability. | Credential loss, downtime, or origin exposure. |
| Cloudflare Zero Trust and DNS | Tunnel route, Access app, Google/OTP methods, `onda.lol` policy, WARP authentication. | Domain-wide admin admission if policy is broader than intended. |
| Separate private macOS repo | Swift menu app, capacity client, Keychain, signing/notarization, DMG, updater. | Native auth/session expiry, stale display, supply-chain/update risk. |
| Employee Codex configuration | Per-person/device API key and gateway base URL. | Secret leakage or incorrect attribution. |

## Acceptance Criteria

| ID | Criterion | Depends On | Proof Capability | Native Surface / Replay | State |
|---|---|---|---|---|---|
| AC1 | The exact Codex-LB commit and container digest pass the predecessor repository checks and fresh independent review before any deployment. | — | execute, assert, capture, reproduce | predecessor AC1-AC7; `bin/test`; image inspection; `me-review` | not_started |
| AC2 | The origin runs from persistent encrypted state on loopback only, survives restart/reboot, and exposes no public application or SSH listener. | AC1 | execute, observe, assert, reproduce, cleanup | listener/firewall probes, container inspection, reboot smoke, paired DB/key restore | not_started |
| AC3 | `tokenmaxxing.onda.systems` reaches the loopback origin only through a named Cloudflare Tunnel; the origin verifies `Cf-Access-Jwt-Assertion` by remote JWKS with exact issuer, audience, expiry, and `@onda.lol` email, derives the actor from that claim, and rejects direct origin, bypass, spoofed-header, forged, expired, wrong-issuer/audience/domain, mismatched-email, and JWKS-failure requests. | AC2 | execute, observe, assert, reproduce, cleanup | Cloudflare API/CLI state, DNS resolution, tunnel config, JWT fixture suite, live allowed token, external negative probes | not_started |
| AC4 | Cloudflare Access presents Google and One-Time PIN as alternative methods and admits an allowed `@onda.lol` address while denying a non-`onda.lol` address. | AC3 | execute, observe, assert, present | real browser sessions for both methods, Access audit events, deny probe, screenshots without cookies/tokens | not_started |
| AC5 | An admitted `@onda.lol` user reaches the unchanged Codex-LB Dashboard, Accounts, and Settings surfaces with full mutation authority and no second login. | AC4 | execute, observe, assert, present | browser navigation, safe reversible settings/API-key canary, visual comparison to upstream screenshots | not_started |
| AC6 | After current grants and sessions are checkpointed, every Prodex/other refresh-capable writer is stopped before import; isolated Codex-LB performs a bounded one-account canary, then all four identities are promoted or Codex-LB stops and the latest grants return to exactly one Prodex writer within the declared outage window. | AC2 | observe, assert, capture, resume | process/profile inventory, auth digests/mtimes, cutover dry-run ledger, zero-writer fence, exact-one-writer success/rollback proof | not_started |
| AC7 | Each of four accounts passes identity, quota, model, and two consecutive real streaming requests through Codex-LB, and rollback can restore the latest grants to one writer. | AC6 | execute, observe, assert, reproduce, resume | per-account bounded canaries, redacted logs, latest-grant export/restore rehearsal | not_started |
| AC8 | Every employee/device gateway credential is manually created and named from an authenticated dashboard identity, unique, attributable, revocable, copied once into Keychain/config with restrictive permissions, and requires both WARP Access and its bearer key; missing/revoked/shared-test keys and key-without-WARP fail. | AC5, AC7 | execute, observe, assert, cleanup | issuance ledger, dashboard API-key flow, key-prefix inventory, WARP+key truth table, revocation within the declared cache window, Keychain/config checks | not_started |
| AC9 | All migrated Codex clients use the Tokenmaxxing gateway, preserve session continuity, and generate request/account usage visible in the dashboard without reactivating Prodex. | AC7, AC8 | execute, observe, assert, resume, present | disposable canary, session retag dry-run/apply, real continued turn, dashboard and process evidence | not_started |
| AC10 | The additive `/api/fleet/summary` response emits one server-authoritative `generatedAt` snapshot with included/excluded account IDs/statuses, per-account windows, Spark/additional quota, five-hour and weekly used percentages, and stale state/reason. Active fresh accounts form the denominator; paused/deactivated accounts are excluded; exhausted fresh accounts count as 100% used; any included account with missing/stale quota makes the aggregate stale and prevents a healthy numeric headline. Fresh percentages are clamped to 0-100 and rounded to the nearest whole percent for display. | AC7 | execute, assert, reproduce | existing fleet integration suite plus additive schema compatibility and frozen truth-table fixtures; same-timestamp live comparison | not_started |
| AC11 | An enrolled `@onda.lol` WARP user with its per-device bearer key can fetch capacity in the macOS app without embedded service credentials; WARP-without-key, key-without-WARP, revoked-key, non-enrolled, and non-domain paths fail. Whole-host Access covers dashboard plus Codex HTTP/SSE/WebSocket routes, so every employee Codex client also requires WARP. | AC3, AC8, AC10 | execute, observe, assert, present | real managed Mac and control device, WARP policy state, HTTP/SSE/WebSocket canaries, redacted logs | not_started |
| AC12 | The menu bar shows five-hour `N% used`; its popover shows weekly/Spark/per-account/reset detail, last refresh and stale/error states, refreshes at the server cadence, and opens the dashboard. | AC10, AC11 | execute, observe, assert, present | unit/UI tests, deterministic fixture server, real-device recording/screenshots, live comparison within tolerance | not_started |
| AC13 | The macOS app installs from a signed/notarized DMG without Gatekeeper warnings, launches at login when enabled, stores secrets only in Keychain, updates through a signed channel, and can roll back to the previous release. | AC12 | execute, observe, assert, reproduce, cleanup | `codesign`, `spctl`, notarization result, clean-Mac install, updater promotion/rollback rehearsal | blocked |
| AC14 | Archive/request/upstream payload logging is explicitly forced off and drift-detected; prompt/response canary text is absent from DB/files/logs; request-level metadata is idempotently hard-deleted on a UTC 30-day boundary; every backup/snapshot containing older identifiable rows expires; v1 retains no long-term request aggregate capable of reconstructing per-user/API-key/session/IP activity. Quota history that contains no employee request identity may remain. | AC1 | execute, observe, assert, reproduce, cleanup | config drift check, canary absence scan, retention scheduler boundary/crash-retry tests, live redacted oldest-row query, restored-backup and age-expiry proof | not_started |
| AC15 | After cutover, 15 minutes of host, tunnel, Access, account, gateway, retention, and client signals remain within declared bands or the single-writer rollback restores service and sessions. | AC9, AC12, AC14 | observe, assert, capture, cleanup, resume | timestamped observation ledger, dashboards/logs/metrics, declared rollback replay | not_started |

Only a fresh `me-review` grader may mark criteria `passing`. AC13 remains
`blocked` until Apple credential presence and identifiers are verified without
exposing secret values.

## Evidence Plan

| AC | Claim | Capability | Native Surface / Replay Inputs | Artifact / Human Presentation | Cleanup / Resume | Result / Limits |
|---|---|---|---|---|---|---|
| AC1-AC3 | Reviewed immutable origin is private behind the intended Tunnel. | execute/observe/assert/capture | exact SHA/digest, host identity, CF account/zone/app IDs, listener and negative probes | deploy decision packet plus topology and probe transcript | remove route/container or restore prior tunnel config | not verified; no external state read or changed here |
| AC4-AC5 | Both login methods enforce `@onda.lol` and yield one dashboard login layer. | execute/observe/assert/present | real allowed and denied identities in clean browser profiles | redacted screenshots and Access audit IDs | revoke test sessions and temporary canary key | not verified; human/browser proof required |
| AC6-AC9 | Four-account single-writer cutover and attributed client traffic work. | execute/observe/assert/resume | checkpoint ledger, account identities, per-device keys, bounded real requests | redacted cutover/continuity ledger | latest-grant rollback and exact session resume gate | not verified; credential mutation is fenced |
| AC10 | Existing fleet summary additively owns normalized utilization and stale semantics. | execute/assert/reproduce | compatibility + truth-table fixtures and same-timestamp live snapshot | table comparing included/excluded source windows to displayed used/stale result | fixture cleanup; no external effect | not verified |
| AC11-AC13 | Managed Mac auth, UX, installation, update, and rollback work. | execute/observe/assert/present | clean enrolled/control Macs, signed artifacts, fixture/live endpoints | screenshots/recording plus signing/notarization transcripts | uninstall/revoke/update rollback instructions | blocked on Apple credentials and real Macs |
| AC14 | Data minimization and 30-day deletion hold across primary DB and backups. | execute/observe/assert/cleanup | boundary timestamps, schema, retention invocation, backup lifecycle | redacted row-count/age evidence | restore test DB only; never restore expired personal data | not verified |
| AC15 | Promoted system remains healthy or rolls back safely. | observe/assert/capture/resume | predeclared 15-minute bands and tripwires | observation or rollback ledger | exact healthy endpoint/session resume state | not verified |

Every material record must include run/criterion, exact invocation, inputs and
target identity, SHA/digest, result, authoritative artifact, limits, external
effects, cleanup, and resume gate. Cookies, OAuth grants, API keys, Apple private
keys, notarization credentials, HARs, and unredacted database rows are never
evidence.

## Assumptions

| Severity | Assumption | Why acceptable / blocker |
|---|---|---|
| Material | Cloudflare account controls `onda.systems`, a Zero Trust org, Google IdP, OTP, Tunnel, and WARP enrollment. | Must be verified by status/IDs before mutation; wrong account is a hard stop. |
| Material | Every intended administrator controls an `@onda.lol` mailbox and domain-wide admin access is intentional. | Explicitly confirmed by the user; still a wide residual risk. |
| Material | Fresh percentage windows from the four same-class current accounts are comparable enough to average into a normalized utilization indicator. | Per-account values/reset times and included/excluded sets remain visible; missing/stale included data suppresses the healthy headline instead of being flattened. |
| Blocking | Apple Developer ID, notarization, and update-signing credentials exist or can be provisioned. | AC13 and employee release remain blocked until verified. |
| Minor | A separate private Onda macOS repository can be created. | Reversible implementation default; avoids upstream dashboard merge burden. |

## Decisions

| Decision | Alternatives considered | Reason |
|---|---|---|
| Keep upstream dashboard unchanged. | New read-only UI; visual rebrand. | User chose existing interface and original appearance. |
| One Access layer with Google or OTP for all `@onda.lol`. | Two-factor Codex-LB login; explicit-email allowlist. | User chose domain-wide alternative methods. |
| Give every admitted user full admin access. | Read-only users plus admin allowlist/RBAC. | Explicit user choice despite the broader blast radius. |
| Use WARP identity for the native client. | Embedded service token; custom browser-token handoff. | No shared client secret and one managed identity boundary. |
| Normalize headline utilization to 0-100. | Summed remaining percentages; lowest-account value. | Matches the user's mental model: unused pool is 0% used. |
| Per-user/device gateway keys without initial caps. | Shared key; immediate hard caps. | Attribution/revocation now, policy based on measured usage later. |
| Thirty-day request metadata with no employee-level request history afterward. | Indefinite request ledger; no diagnostics. | Bounded debugging value without indefinite employee tracking. |
| No long-term employee-level request aggregate in v1. | New rollup table with irreversible aggregation. | Smaller privacy surface and no undefined aggregate grain; quota history remains sufficient for capacity. |
| Tunnel to loopback origin. | Public host ports; old Tailscale Serve-only ingress. | Supports the required hostname and Access while preserving a closed origin. |

## Controversial Decisions

- Every `@onda.lol` mailbox holder receives destructive dashboard and API-key
  administration. This is a deliberate user decision, not an inferred default.
- OTP makes mailbox control sufficient for admission even when Google group
  claims are unavailable. The Access policy must enforce the email domain for
  both methods.
- The dashboard stays branded `Codex LB` although the service hostname is
  Tokenmaxxing.

## Stop Conditions

| Condition | Reason | Required Action |
|---|---|---|
| Any origin path bypasses Access/Tunnel or trusts a caller-supplied identity header. | Full administrative compromise. | Remove route, stop deployment, rotate affected sessions/keys, and re-prove boundary. |
| Any non-`onda.lol` identity is admitted, or either chosen method bypasses the same domain rule. | Policy scope breach. | Disable application policy and inspect Access audit/config before retry. |
| Any secret appears in output, git, image, app bundle, update feed, logs, screenshots, or broad-permission file. | Credential compromise. | Stop, revoke/rotate, scrub untrusted artifact, and restart from clean proof. |
| Any old refresh writer remains when import starts. | OAuth rotation can strand all clients. | Abort import; restore zero-writer fence and checkpoints. |
| Capacity data is stale but displayed as current, or utilization leaves 0-100. | Misleading primary product behavior. | Fail visibly and block promotion. |
| Prompt/response content is persisted or identifiable request metadata survives beyond 30 days. | Privacy contract breach. | Stop promotion, delete prohibited copies, and prove erasure before resuming. |
| DMG/update is unsigned, unnotarized, mutable, or fails clean-Mac Gatekeeper/update rollback. | Client supply-chain and adoption risk. | Keep release blocked; do not instruct users to bypass Gatekeeper. |
| Same criterion fails twice for the same cause. | Retry loop without new evidence. | Mark blocked and return the exact missing prerequisite/decision. |

## Residual Risks

- Domain-wide administrators can delete accounts, change routing/firewall, or
  issue unrestricted gateway keys; Access auditability reduces but does not
  remove this blast radius.
- Compromise of any `@onda.lol` mailbox permits OTP entry until the Access
  policy or mailbox is disabled.
- Single-node SQLite plus matching encryption key remains a root/host failure
  boundary; paired encrypted backups and rehearsed restore mitigate availability,
  not host compromise.
- Averaging percentages is a normalized availability indicator, not a literal
  token counter when upstream account plans/windows differ.
- WARP enrollment and Apple signing add external control-plane dependencies.
- Whole-host Access means every Codex CLI/API client must run on an enrolled
  WARP device in v1; headless/non-WARP clients are intentionally unsupported.
- Codex-LB upstream changes may conflict with local retention/auth adapters;
  exact-version proof is required on every upgrade.

## Next Route

Needs engineering review before implementation because the revised Cloudflare
ingress, trusted-header boundary, WARP-native client authentication, retention,
and Apple update path cross security and client-release boundaries:
`me-review-technical-approach`, then `me-build-and-prove`, fresh `me-review`, and
`me-land` for separately authorized cloud/cutover/release effects.

## File Structure

- Codex-LB/OpenSpec/deployment changes remain in the existing task worktree.
- macOS source, release automation, signing configuration, and update feed live
  in a separate private Onda repository; no Apple private key is stored there.
- Cloudflare/Hetzner/Apple evidence is stored under ignored, access-controlled
  artifact paths with redacted durable summaries in the relevant evidence
  ledger.
