# Add anonymous telemetry (informed opt-out)

## Problem

codex-lb has grown to ~2.6k stars, ~2.2k unique cloners per 14 days, and an unknown number of
running instances. The project has zero visibility into its install base: version distribution
(how many instances still run pre-1.16 with known bugs), database backend split (SQLite vs
Postgres), transport adoption (WebSocket vs HTTP bridge), deployment shape (docker/helm/pip),
client ecosystem (codex-cli vs SDK integrations), or which optional modules are actually used.
Every roadmap and deprecation decision is currently guesswork. The only existing outbound
signal is the update check in `app/modules/runtime/service.py` (GitHub releases poll), which
proves instances phone GitHub already but gives the project nothing.

## Solution

Add a new `telemetry` capability: an anonymous, schema-allowlisted usage snapshot sent to a
project-operated collection endpoint (self-hosted SHM server, `https://telemetry.tokmaxxing.com`)
at startup and every 24 hours. Consent is **informed opt-out**: telemetry is active by default,
every user (new and upgrading) gets a one-time dashboard dialog showing the exact JSON payload
before deciding, a persistent settings toggle, an environment variable kill switch for headless
deployments, and a startup log notice while consent is undecided.

All payload fields are derived from data codex-lb already stores (`request_logs`, settings,
module registry). No new per-request instrumentation is added. The payload is strictly
allowlisted: raw user-agent strings, custom model names, emails, workspace IDs, IPs, prompts,
API keys, and per-account records are never transmitted. Client statistics go through a
canonical client-family mapping table (raw UA groups like `senpi` must never leave the
instance); model statistics go through the official model catalog allowlist.

## Why this is correct as a behavior change

- This is a new operator-visible contract (outbound network traffic + consent flow), which is
  exactly the class of change OpenSpec gates. The delta spec makes the privacy allowlist
  normative and testable so it cannot regress silently.
- Default-on telemetry in a privacy-sensitive user base is defensible only if the allowlist,
  the payload preview, and the kill switches are hard requirements, not implementation
  details. Encoding them as MUST requirements with regression tests is the mitigation.
- No existing client or operator behavior changes: proxying, routing, and dashboards are
  unaffected; telemetry failure is isolated by requirement.

## Changes

### Spec deltas

- `telemetry` (new capability): payload allowlist, consent state machine, one-time dialog with
  exact payload preview, env kill switch, headless notice, client-family mapping table, model
  catalog allowlist, random instance identity, transmission cadence + failure isolation,
  bucketed sensitive aggregates.

### Code

- `app/modules/telemetry/` (new module) — snapshot builder (aggregation queries over
  `request_logs` + settings introspection), consent state, scheduler (startup + 24h), sender
  (bounded timeout, fire-and-forget).
- `app/core/config/settings.py` — `telemetry_enabled: bool | None = None` (tri-state; env
  `CODEX_LB_TELEMETRY_ENABLED` maps to it), `telemetry_endpoint` (default
  `https://telemetry.tokmaxxing.com`).
- `app/db/models.py` + Alembic migration — persisted consent decision + `telemetry_instance_id`
  (random UUID minted on first run).
- Dashboard (frontend) — one-time consent dialog with payload preview; Settings toggle.
- `app/main.py` — scheduler wiring + undecided-consent startup notice.

### Tests

- Unit: payload builder allowlist (schema snapshot test — any new field fails the test until
  spec updated), client-family mapping (every observed raw group → family, unknown → `other`),
  model allowlist, bucket edges, consent resolution precedence (env > persisted > default).
- Integration: consent API endpoints; disabled ⇒ zero outbound calls (socket-level assert);
  telemetry endpoint unreachable ⇒ proxy path unaffected.
- Migration smoke: new columns present with correct defaults.

## Out of scope

- The SHM collection server deployment itself (infra task, separate from this repo).
- Public aggregate dashboard / README badges (consumes collected data; follow-up).
- Any new per-request instrumentation or Prometheus metric changes.
- Crash/error report collection (stack traces are content-adjacent; deliberately excluded).
