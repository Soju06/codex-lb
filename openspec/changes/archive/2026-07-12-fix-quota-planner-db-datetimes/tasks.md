- [x] Add an OpenSpec requirement that the quota phase planner MUST normalize
  timezone-aware instants to naive UTC before persisting them to the
  timezone-naive `QuotaPlannerDecision.scheduled_at` / `executed_at` columns,
  while JSON audit snapshots may keep ISO offset strings.
- [x] Add a RED regression test asserting `QuotaPlannerRepository.log_decision`
  and `update_decision_status` sanitize aware `scheduled_at` / `executed_at`
  into naive UTC while preserving the instant.
- [x] Normalize aware datetimes to naive UTC in the repository insert and update
  paths via a small explicit helper; leave naive inputs unchanged.
- [x] Run focused quota planner unit and integration tests.
- [x] Run OpenSpec strict validation for this change.
