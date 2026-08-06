# Tasks

## Implementation

- [ ] T1: `app/modules/telemetry/` module — snapshot builder aggregating `request_logs`
  (7d window, reusing reports-module aggregate shapes), settings/module introspection,
  bucket helpers, client-family mapping table, model catalog allowlist filter.
- [ ] T2: Consent state — DB columns (`telemetry_consent`, `telemetry_instance_id`) +
  Alembic migration on current main head; resolution precedence env > persisted > default.
- [ ] T3: Settings — `telemetry_enabled: bool | None` (env `CODEX_LB_TELEMETRY_ENABLED`),
  `telemetry_endpoint` default `https://telemetry.nekos.me`.
- [ ] T4: Sender — SHM `/v1/register` + `/v1/activate` + `/v1/snapshot` client (Ed25519
  keypair per instance), 5s timeout, ≤1 retry/interval, debug-only failure logs.
- [ ] T5: Scheduler — startup snapshot + 24h interval; undecided-consent startup notice
  (single log line with docs link + disable instructions).
- [ ] T6: Dashboard consent dialog — one-time while undecided, renders live payload JSON,
  equal-prominence enable/disable; Settings toggle wired to consent API.
- [ ] T7: Consent API endpoints (get resolved state, set decision).

## Spec

- [ ] T8: Apply delta `specs/telemetry/spec.md` as new capability; sync payload schema into
  `openspec/specs/telemetry/context.md`.

## Validation

- [ ] T9: Unit — schema snapshot allowlist test (undeclared field ⇒ fail), client mapping
  (all observed raw groups + unknown ⇒ `other`), model allowlist, bucket edges, consent
  precedence.
- [ ] T10: Integration — consent endpoints; disabled ⇒ zero outbound connections
  (socket-level); endpoint unreachable ⇒ proxy unaffected.
- [ ] T11: Migration smoke — new columns/defaults present (SQLite + Postgres).
- [ ] T12: Privacy quick check from context.md reproduced as a test (identifying strings
  absent from serialized payload).
- [ ] T13: `openspec validate add-anonymous-telemetry` → valid; `make lint`; targeted +
  broader pytest sweeps.
