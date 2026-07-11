# me-security: Tokenmaxxing post-build server diff

Mode: independent read-only review
Session kind: autonomous
Execution status: complete
Parent run: `20260711-tokenmaxxing`
Scope: current uncommitted Tokenmaxxing server diff in
`/code/codex-lb/.worktrees/me-setup-repo`; Cloudflare Access JWT boundary,
API-key capacity privacy, request-log retention, secret/payload handling, and
deployment configuration
Scope source: delegated LFG security re-audit plus
`openspec/changes/secure-tokenmaxxing-boundaries/context/skill-outputs/me-define-done/2026-07-11-tokenmaxxing.md`
Review class: read-only security review; no external state or secrets read

Selected principles:

- `trust-boundaries.md`: traced identity from the socket peer and Access
  assertion through middleware into the dashboard principal, including denied
  claims.
- `least-privilege-access.md`: checked API-key account scoping and quota
  visibility rather than treating possession of any key as fleet-wide access.
- `structural-safety-boundaries.md`: required deployment configuration to make
  JWT validation, loopback binding, and payload minimization mandatory rather
  than operator conventions.
- `right-to-erasure.md`: traced identifiable request metadata into primary and
  backup copies and required age-bounded deletion of both.
- `fail-fast-vs-degrade.md`: treated uncertain authentication as fail-closed and
  deletion failure as an operator-visible degraded state.
- `name-residual-risk.md`: kept live Access, WARP, origin, backup, and payload
  absence claims unverified where this disk-only review cannot prove them.

## Verdict

**BLOCK DEPLOYMENT.** The configured JWT validator, fleet API-key privacy, and
primary request-log deletion primitives are materially improved and their
targeted tests pass. The current disk state does not, however, contain a safe
Tokenmaxxing deployment configuration that requires those controls. Existing
production Compose publishes the application ports on all interfaces, Access
JWT validation remains optional, backups have no age-based expiry, and payload
minimization is a default rather than a drift-resistant production invariant.

## Findings

### High: trusted-header mode still permits deployment without Access JWT validation

- Category: authentication bypass / insecure configuration
- Affected asset: full Codex-LB dashboard administration, accounts, OAuth
  grants, routing, and API-key issuance
- Exploit condition: Tokenmaxxing starts in `trusted_header` mode with trusted
  proxy CIDRs but without all Access JWT settings. Startup accepts this state;
  requests from a trusted socket peer retain `Remote-User` without
  cryptographic validation. A compromised/misconfigured process or reachable
  peer in that CIDR can choose any administrator identity.
- Evidence: `app/core/config/settings.py:606-623` rejects only *partial* Access
  configuration; all three Access values may be absent. In that state,
  `app/core/middleware/dashboard_auth_proxy.py:59-72` takes the legacy trusted
  source branch and forwards the identity header unchanged. `.env.example:69-79`
  labels the Access values optional. No Tokenmaxxing deployment manifest is
  present (`rg tokenmaxxing` finds only planning artifacts).
- Fix route: add an explicit production requirement (or a dedicated auth mode)
  that makes issuer, application audience, allowed domain, and assertion header
  mandatory for Tokenmaxxing; add a startup-negative test proving the exact
  production profile cannot fall back to raw `Remote-User`.
- Residual risk after fix: the cloudflared socket/CIDR and exact Access app AUD
  still require live provider and origin-bypass proof.

### High: repository production Compose violates the loopback-only origin boundary

- Category: network exposure / boundary bypass
- Affected asset: dashboard and OpenAI-compatible proxy endpoints
- Exploit condition: an operator deploys the repository's documented
  `docker-compose.prod.yml` on the target host. Docker publishes ports 2455 and
  1455 on every host interface, allowing traffic to reach the origin outside
  the intended Tunnel/Access path unless an independently verified firewall
  happens to compensate.
- Evidence: `docker-compose.prod.yml:15-17` uses `2455:2455` and `1455:1455`,
  not `127.0.0.1:...`; the same public-binding shape exists in
  `docker-compose.yml`. There is no dedicated Tokenmaxxing compose/systemd
  artifact or checked firewall/tunnel configuration in the diff.
- Fix route: provide a dedicated reviewed deployment artifact with loopback-only
  bindings (or no published container ports), a named tunnel route, firewall
  policy, and executable listener/origin-bypass checks; do not use the current
  generic production Compose for this host.
- Residual risk after fix: live cloud firewall, Docker forwarding, tunnel ingress,
  and host listener state still need external negative probes.

### High: 30-day erasure does not cover database backups or snapshots

- Category: privacy / retention
- Affected asset: API-key, session, IP, user-agent, account, model, error, and
  timing metadata in `request_logs`
- Exploit condition: the primary scheduler deletes a row after 30 days while a
  pre-migration, host, provider, or copied database backup containing that row
  remains restorable beyond the promised period.
- Evidence: primary deletion exists at
  `app/modules/request_logs/repository.py:31-40`. The repository backup helper
  at `app/db/backup.py:41-63` rotates only by file count, not age or row
  retention. No Tokenmaxxing backup/snapshot expiry policy, restored-backup
  scrub, or test exists in the diff. The data includes direct correlators at
  `app/db/models.py:197-214`.
- Fix route: define every backup/snapshot class, enforce a maximum age compatible
  with the 30-day promise (or scrub request rows before/after backup), and prove
  expiry plus a restored-backup oldest-row check.
- Residual risk after fix: provider-side delayed deletion and immutable backup
  windows must be documented as an explicit privacy residual.

### Medium: payload non-retention is not structurally enforced and error bodies can enter metadata

- Category: sensitive data persistence / logging
- Affected asset: prompt/response content and provider error text
- Exploit condition: production configuration enables conversation archival, or
  an upstream/model-source error message echoes user content or sensitive
  provider text. The archive stores full bodies; request logs store upstream
  error messages and failure details for up to the retention limit.
- Evidence: `.env.example:91-98` defaults archival off but leaves both archival
  and retention deployment-selectable, with retention commented out. There is
  no Tokenmaxxing drift check or production assertion. Upstream error messages
  are copied verbatim by `app/core/clients/proxy.py:771-777` and
  `app/modules/proxy/api.py:5776-5781`; `RequestLog.error_message` and
  `failure_detail` are text columns at `app/db/models.py:237-240`.
- Fix route: make archive/payload settings immutable-off in the Tokenmaxxing
  deployment, add a startup/config drift check, redact or classify persisted
  error fields, and run a unique canary-content absence scan across DB, files,
  and logs.
- Residual risk after fix: arbitrary provider error text needs a maintained
  redaction policy; absence scans prove only the exercised paths.

### Medium: cleanup failure is silent to health and can violate the retention promise indefinitely

- Category: privacy control observability
- Affected asset: all request-level metadata subject to 30-day deletion
- Exploit condition: database write, leader election, or scheduler execution
  repeatedly fails. Cleanup returns zero or logs an exception, but readiness
  remains healthy and no age watermark/metric trips deployment observation.
- Evidence: `app/modules/request_logs/cleanup_scheduler.py:59-72` returns zero
  when leadership is unavailable and catches all deletion exceptions. The diff
  adds no oldest-row gauge, cleanup-success timestamp, readiness condition, or
  alert. Unit tests mock a successful leader/delete path but do not prove
  crash/retry or persistent-failure visibility.
- Fix route: expose last successful cleanup and oldest identifiable row age,
  alert before the retention ceiling, and test transient retry plus persistent
  failure signaling.
- Residual risk after fix: a complete observability outage can still delay
  detection and needs an operational reconciliation job.

## Explicit clears

- Lens: configured Cloudflare Access JWT validation
  Verdict: clear at the unit boundary
  Evidence: `app/core/middleware/dashboard_auth_proxy.py:59-114` strips the raw
  identity/assertion, verifies RS256 signature plus exact issuer, configured
  audience, expiry, and email domain, then derives normalized `Remote-User`
  only from the validated claim. `tests/unit/test_dashboard_access_jwt.py`
  covers valid, missing, forged, expired, wrong issuer, wrong audience, wrong
  domain, and JWKS failure. The fresh targeted run passed all 33 selected tests.
  Limit: the test substitutes JWKS and inspects the derived header; no live
  Cloudflare token, real JWKS rotation, or full dashboard denied-path was used.

- Lens: API-key fleet capacity privacy
  Verdict: clear at route/integration scope
  Evidence: `app/modules/fleet/api.py:46-90` requires a valid bearer API key,
  applies assigned-account scoping, and emits utilization/additional quota only
  when both required usage sections and the global privacy setting allow it.
  `tests/integration/test_fleet_summary_api.py:224-490` proves missing/invalid
  key denial, sensitive-field exclusion, account scoping, and quota hiding.
  Limit: permitted responses intentionally include account email/display name,
  status, and plan; any issued unscoped key with both usage permissions can see
  the whole pool. This is consistent with the current all-admin product choice,
  not least privilege for a lost device key.

- Lens: primary request-log hard deletion
  Verdict: clear at repository boundary
  Evidence: `app/modules/request_logs/repository.py:31-40` performs a hard delete
  for rows strictly older than the cutoff and commits atomically.
  `tests/integration/test_request_log_retention.py:25-44` proves expired-only,
  exact-cutoff retention, and idempotent replay. Scheduler startup/shutdown and
  settings wiring are present in `app/main.py` and unit-tested.
  Limit: primary-db proof does not establish backups, live scheduling, crash
  recovery, monitoring, or production configuration.

- Lens: secret handling in this diff
  Verdict: clear by source inspection
  Evidence: fleet responses do not add OAuth tokens, API-key plaintext, Access
  assertions, or JWKS material; JWT rejection logs only exception class. The
  review read no secret stores and printed no credential values.
  Limit: built image, environment, runtime logs, browser artifacts, and cloud
  configuration were unavailable.

## Not verified

- Live Cloudflare Access application, Google/OTP policies, exact AUD/issuer,
  WARP application authentication, tunnel ingress, and non-domain denial.
- Direct-origin denial, host/Docker listener state, firewall state, and
  Cloudflare-header behavior on HTTP/SSE/WebSocket routes.
- Real API-key create/use/revoke behavior and revocation-cache latency.
- Production database oldest-row age, archive directory absence, log-content
  absence, backup inventory/expiry, restore behavior, and scheduler operation.
- Dependency vulnerability audit: `PyJWT[crypto]` is lock-resolved
  (`pyjwt 2.13.0`, `cryptography 49.0.0`) and targeted tests pass, but no
  repository-native vulnerability audit was available or installed during this
  read-only review.

## Stress-test

Claims: 3 broke, 3 held, 0 untestable.

### Claim: Access identity is cryptographically validated when configured — HELD

- Attack: missing, forged, expired, wrong-issuer/audience/domain assertions and
  JWKS failure, each accompanied by a forged `Remote-User`.
- Evidence: fresh targeted pytest run passed the explicit denied cases; source
  strips both headers before deriving the actor.

### Claim: Tokenmaxxing cannot run with raw trusted-header authentication — BROKE

- Attack: omit all three optional Access settings while retaining trusted CIDR
  and `Remote-User` mode.
- Evidence: the settings validator accepts this complete omission and the
  middleware forwards the raw header from a trusted source.

### Claim: the origin is private and Tunnel-only — BROKE

- Attack: deploy the repository production Compose as documented.
- Evidence: both application ports publish on all host interfaces and no
  Tokenmaxxing-specific deployment artifact overrides that behavior.

### Claim: capacity data remains behind API-key policy — HELD

- Attack: call without a key, with an invalid key, with a scoped key, and with
  quota visibility disabled.
- Evidence: integration tests passed and verify denial, scoping, and suppression.

### Claim: primary request rows older than the cutoff are deleted idempotently — HELD

- Attack: place rows one second before, exactly at, and after the cutoff, then
  run deletion twice.
- Evidence: integration test passed with only the pre-cutoff row deleted.

### Claim: the 30-day/payload promise covers every retained copy — BROKE

- Attack: restore an older pre-migration/snapshot copy or enable the archive
  setting; inject canary content through an upstream error message.
- Evidence: backups are count-rotated rather than age-bounded, archive remains
  configurable, error messages persist verbatim, and no canary absence or
  restored-backup proof exists.

## Fresh evidence

```text
$ uv run pytest -q tests/unit/test_dashboard_access_jwt.py tests/unit/test_fleet_capacity.py tests/unit/test_request_log_cleanup_scheduler.py tests/integration/test_request_log_retention.py tests/integration/test_fleet_summary_api.py
.................................                                        [100%]
33 passed in 12.50s
```

`git diff --check` passed. `uv tree --depth 1` resolved the added JWT dependency
without exposing configuration or credential values.

## Routing

Route the five findings back to `me-build-and-prove`. Do not deploy or mutate
Cloudflare/Hetzner state until a fresh `me-review` and security re-audit confirm
a mandatory Tokenmaxxing production profile, loopback-only origin, backup
expiry, payload drift protection, and retention observability. Live cloud,
browser, API-key revocation, and origin-bypass proofs remain subsequent
`me-qa`/`me-land` gates rather than claims this disk audit can satisfy.
