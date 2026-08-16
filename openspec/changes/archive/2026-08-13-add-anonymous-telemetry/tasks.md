# Tasks

## Implementation

- [x] T1: `app/modules/telemetry/` module — snapshot builder aggregating `request_logs`
  (7d window, reusing reports-module aggregate shapes), settings/module introspection,
  bucket helpers, client-family mapping table, model catalog allowlist filter.
- [x] T2: Consent state — DB columns (`telemetry_consent`, `telemetry_instance_id`) +
  Alembic migration on current main head; resolution precedence env > persisted > default.
- [x] T3: Settings — `telemetry_enabled: bool | None` (env `CODEX_LB_TELEMETRY_ENABLED`),
  `telemetry_endpoint` default `https://telemetry.tokmaxxing.com`.
- [x] T4: Sender — SHM `/v1/register` + `/v1/activate` + `/v1/snapshot` client (Ed25519
  keypair per instance), 5s timeout, ≤1 retry/interval, debug-only failure logs.
- [x] T5: Scheduler — startup snapshot + 24h interval; undecided-consent startup notice
  (single log line with docs link + disable instructions).
- [x] T6: Dashboard consent dialog — one-time while undecided, renders live payload JSON,
  equal-prominence enable/disable; Settings toggle wired to consent API.
- [x] T7: Consent API endpoints (get resolved state, set decision).

## Spec

- [x] T8: Apply delta `specs/telemetry/spec.md` as new capability; sync payload schema into
  `openspec/specs/telemetry/context.md`.

## Validation

- [x] T9: Unit — schema snapshot allowlist test (undeclared field ⇒ fail), client mapping
  (all observed raw groups + unknown ⇒ `other`), model allowlist, bucket edges, consent
  precedence.
- [x] T10: Integration — consent endpoints; disabled ⇒ zero outbound connections
  (socket-level); endpoint unreachable ⇒ proxy unaffected.
- [x] T11: Migration smoke — new columns/defaults present (SQLite + Postgres).
- [x] T12: Privacy quick check from context.md reproduced as a test (identifying strings
  absent from serialized payload).
- [x] T14: Review remediation — shared preview/sender envelope, typed allowlisted bodies,
  preview-on-demand consent API, and wire-schema regressions.
- [x] T15: Review remediation — leader-gated telemetry ticks plus real lifespan wiring and
  non-leader regression coverage.
- [x] T16: Review remediation — fail-honest request kinds, derived routing/client allowlists,
  honest database-size failure reporting, and typed query expressions.
- [x] T17: Publish anonymous telemetry documentation and register it in the docs navigation.
- [x] T13: `openspec validate add-anonymous-telemetry` → valid; `make lint`; targeted +
  broader pytest sweeps.
