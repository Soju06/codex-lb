## Context

Upstream auth and usage payloads contain plan identifiers absent from the shared
normalizer. This can reject legitimate usage refreshes and cause capacity or
model selection to treat a paid account as unknown.

## Decisions

- Canonicalize `self_serve_business_prolite` through the shared account-plan
  normalizer so account, usage, eligibility, and rate-limit callers agree.
- Keep `promax` distinct for storage and display, with Pro model eligibility.
- Reuse the existing Pro capacity estimates of 1500 primary and 50400 secondary
  credits; these are estimates, not measured Pro Max allowances.
- Preserve workspace identity checks and all quota-status recovery policy.
- Reuse the shared plan normalizer for telemetry and keep Pro-family accounts
  inside the existing coarse Pro bucket without adding payload fields.

## Verification

Cover account-plan boundaries, usage refresh writes, aggregate capacity,
Pro-gated account selection, capacity-aware routing, dashboard summaries, and
the serialized telemetry snapshot.
