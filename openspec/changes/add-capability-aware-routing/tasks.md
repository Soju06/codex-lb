## 1. Durable lineage

- [x] 1.1 Add failing repository regressions for monotonic persistence,
  API-key isolation, opaque storage, and fresh-instance recovery; then add the
  marker model, focused migration, and repository contract.
- [x] 1.2 Prove upgrade/downgrade behavior and a single Alembic head without
  modifying existing table contracts or backfilling rows.

## 2. Direct WebSocket capability routing

- [x] 2.1 Add failing parser and privacy regressions; then implement strict
  authenticated header/per-frame metadata parsing and carrier stripping.
- [x] 2.2 Add failing first-selection and empty-pool regressions; then establish
  the capability requirement before canonical account selection.
- [x] 2.3 Add failing no-echo and echoed-turn-state reconnect regressions; then
  restore and propagate the API-key-scoped requirement across accepted session,
  synthesized turn-state, previous-response, and task aliases.
- [x] 2.4 Add failing late-capability regressions; then retire an idle ordinary
  upstream before capable reselection and fail closed while ordinary work is
  pending.

## 3. Verification and publication readiness

- [x] 3.1 Run strict scoped and repository OpenSpec validation, focused unit and
  direct-WebSocket integration tests, migration checks, architecture checks,
  affected lint/format/type gates, `git diff --check`, and the repository local
  CI gate.
- [x] 3.2 Inspect the final committed diff, run one independent Sensitive review,
  and resolve every actionable in-scope finding before publication.
