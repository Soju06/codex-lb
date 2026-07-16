# Context: reduce-settings-surface-phase-4

## Rationale

The prewarm canary settings were rollout tooling, not product surface. They
existed to answer one question — "does prewarming Codex bridge sessions
improve TTFT without side effects?" — by deterministically sampling a
percentage of eligible requests and scoping the experiment with API-key
allow/deny lists. That question is answered by the recorded outcome data;
keeping three env-settable fields (plus a validator, two request-log
columns, and two Prometheus label dimensions) for a finished experiment
violates PRINCIPLES.md P2.

The `prewarm_enabled` flag survives because the feature itself is still
mid-rollout: it defaults off, and enabling it is a real operator decision.
What goes away is only the *scoping* machinery around that decision.

## Production verification (2026-07-15)

Verified live before removal, across all replicas:

- `http_responses_session_bridge_codex_prewarm_enabled=False` everywhere
- `http_responses_session_bridge_codex_prewarm_canary_percent` unset (`None`)
- allow/deny API-key-id lists empty

So no deployment depends on canary sampling or cohort scoping, and the
`canary_percent=None` code path (treat all eligible requests) is exactly the
new unconditional behavior.

## Behavior notes

- With the percent unset, the old code returned the `treatment` bucket with
  the `legacy_all` cohort for every enabled request — the eligibility
  heuristic (`first_turn_50k_gap_2m`: first turn, >=50 KiB input, >=2 min
  session gap) only gated requests when a percent was configured. Removing
  the heuristic together with the sampling therefore changes nothing for
  the verified production configuration or for defaults.
- `prewarm_status=canary_miss` had exactly one source (deterministic
  sampling excluding an eligible request); it is now unreachable and is
  removed from the observability contract. All other statuses
  (`not_applicable`, `skipped`, `success`, `timeout`, `error`) and
  `prewarm_latency_ms` are unchanged.
- Historical `prewarm_canary_bucket` / `prewarm_eligible_reason` request-log
  values stop being written; the columns stay declared on `RequestLog`
  (deprecated, unwritten) for one release so old replicas keep inserting
  safely during rolling upgrades — the Helm migration job is a pre-upgrade
  hook while the workload rolls. The Alembic drop revision ships in the next
  release; the canary experiment data the columns carried has served its
  purpose.

## Rollout note

The canary tooling was one-time. If a future feature needs percentage or
cohort-scoped rollout, that is a new OpenSpec change with its own design —
re-introducing these settings verbatim is explicitly not the path.
