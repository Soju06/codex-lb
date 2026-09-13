## Context

The existing change records SQLite run state in a sidecar and holds a
process-lifetime SQLite sentinel while startup and shutdown use the database.
The remaining correctness gap is that several bounded shutdown paths can leave
database-owning work pending while the lifespan still records `clean`. A failed
`running` transition can also return after cleanup whose durability was not
confirmed. See `proposal.md` for the startup-scan motivation and the delta
specification for the normative behavior.

## Goals / Non-Goals

**Goals:**

- Make `clean` a proof that every database-owning shutdown drain completed.
- Make failed `running` invalidation fail startup closed when removal or its
  directory sync is not durable.
- Make each database-owning shutdown drain an explicit input to the clean
  transition so a bounded timeout cannot certify an incomplete shutdown.
- Preserve the existing lifetime-lock, identity-fence, atomic-write, and final
  revalidation seams, and prove the lock across process boundaries.

**Non-Goals:**

- Change the supported SQLite topology; SQLite remains a single-process
  deployment contract.
- Change the configured `quick`, `full`, or `off` startup-check modes.
- Change PostgreSQL teardown, non-SQLite startup, attribution, or unrelated
  shutdown work.

## Decisions

- `close_db()` returns whether the reclaimed SQLite teardown registry fully
  drained. The lifespan combines that result with the final proxy persistence,
  detached audit/fleet control-plane, and scheduler leader-release results
  before writing `clean`.
- A failed `RUNNING` write removes the temporary and target sidecars and
  directory-syncs the removal. If any removal or sync cannot be confirmed,
  `write_sqlite_runstate()` raises a durability error. Startup runs the
  configured check when enabled, then refuses to continue with a potentially
  trusted marker. A successful invalidation may still return `False`, which
  forces the integrity scan while allowing the caller to continue.
- `close_db()`, HTTP bridge durable-session marking/closure, final proxy
  persistence, detached audit/fleet drains, and leader release each report
  completion. The lifespan records `clean` only when every report is true and
  database disposal itself completed.
- The existing persistent sentinel remains a SQLite `BEGIN IMMEDIATE`
  transaction held until the clean-transition attempt. It is never unlinked;
  SQLite releases the transaction if the owner process dies. Subprocess tests
  cover contention, release after process death, and the startup ordering seam.
- The existing `dev`, `ino`, `size`, `mtime_ns`, and `ctime_ns` identity and
  randomized `mkstemp` write path remain authoritative. Path spelling
  canonicalization is intentionally not added here; aliases require a separate
  ownership-contract decision.

## Risks / Trade-offs

- [Risk] A slow or permanently wedged shutdown now leaves the next startup
  scanning, increasing restart time. → [Mitigation] Keep the existing bounded
  drains and make the fallback conservative rather than claiming `clean`.
- [Risk] A failed sidecar cleanup can prevent startup even when the database is
  otherwise usable. → [Mitigation] This is the required fail-closed behavior;
  operators can repair the sidecar/storage and retry.
- [Risk] Subprocess tests can become timing-sensitive. → [Mitigation] Use
  explicit readiness pipes/events, a zero-timeout lock acquisition, and bounded
  joins with captured child exit status.

## Migration Plan

No database migration is required. Existing missing or unknown sidecars force
the configured integrity check. Deploy the code, run the affected tests and
OpenSpec validation, then verify the hosted checks on the rebased head before
using the optimization in production.
