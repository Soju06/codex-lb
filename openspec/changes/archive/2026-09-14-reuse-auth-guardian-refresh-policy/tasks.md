- [x] Update `usage-refresh-policy` so Auth Guardian shares the canonical
  proactive credential-refresh predicate and carries no background-only age
  threshold.
- [x] Remove the guardian 12-hour constant, scheduler field, and candidate
  parameter; delegate candidate selection and the fresh per-account recheck to
  the shared predicate, then preserve forced execution only after admission.
- [x] Update guardian tests and scheduler integration construction, including
  regressions proving a thirteen-hour-old account is still skipped and the
  guardian inherits the shared policy.
- [x] Update stable context and translated dashboard copy that currently say
  twelve hours.
- [x] Run focused backend/frontend checks, OpenSpec validation, repository
  lint/type checks as practical, and review the final diff before committing.
- [x] Review follow-up: distinguish scan cadence from batch admission and
  active failure backoff in the normative and explanatory timing contract.
