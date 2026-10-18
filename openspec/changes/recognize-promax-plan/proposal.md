## Why

OpenAI auth and usage payloads report `plan_type` `promax` for ChatGPT Pro Max
accounts. Codex-lb does not list `promax` as an account plan, so
`capacity_for_plan` returns no capacity for these accounts and the dashboard
reports no credits for them. The balancer's capacity fallback then scores them
as the unknown-plan default, `free` (1,134 weekly credits).

Capacity-weighted and relative-availability routing therefore weight a Pro Max
account at roughly 2% of a Pro account with the same usage. New work drains
Pro, Team and Plus accounts first while the largest allowance in the pool stays
mostly idle. In one pool observed by the author, Pro Max accounts used a
smaller share of their weekly window than Pro accounts for comparable request
volume, which is consistent with a Pro Max allowance at or above Pro.

## What Changes

- Recognize `promax` as an account plan type.
- Use the Pro capacity values (`1500.0` primary, `50400.0` secondary) for
  `promax` until measured Pro Max values exist. Pro Max is a higher tier than
  Pro, so these values are a floor rather than an estimate of the full
  allowance.
- Treat `promax` as Pro-equivalent for model registry plan eligibility, as
  `prolite` already is.
- Count `promax` with `pro` in the telemetry plan bucket.
- Preserve the stored `promax` value for display and request-log context.

## Impact

- Affected capability: `usage-refresh-policy`.
- Pro Max accounts contribute capacity to dashboard summaries and are weighted
  at least like Pro accounts by capacity-aware routing.
- Existing plans, capacity values and the unknown-plan fallback are unchanged.
- No migration, setting, or account mutation is required.
