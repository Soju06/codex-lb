# Tasks

- [x] Add OpenSpec deltas for runtime observability and admission-control behavior.
- [x] Add focused RED tests for request-log phase fields, stuck-gate retirement, prewarm canary sampling, and prewarm metadata recording.
- [x] Add Alembic migration, SQLAlchemy model fields, repository plumbing, and proxy request-log write plumbing.
- [x] Track response-create gate wait, bridge queue wait, upstream response.created, first upstream event, session gap, and prewarm canary metadata on bridge request state.
- [x] Emit low-cardinality Prometheus metrics for phase latency, prewarm outcomes, and stuck-gate retirements.
- [x] Add stuck `response_create_gate` session retirement with a configurable threshold and safe visible-pending guard.
- [x] Add deterministic API-key/session prewarm canary sampling and first-turn/large-input/gap cohort eligibility.
- [x] Add 24-hour TTFT SQL runbook and Grafana dashboard JSON.
- [x] Run focused tests, OpenSpec validation, lint/type checks as practical, and record RED/GREEN evidence in `/tmp/ttft-phase-observability-verification.md`.
