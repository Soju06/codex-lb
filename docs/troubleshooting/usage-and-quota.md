# Usage and quota troubleshooting

Common questions about how codex-lb derives an account's usage and status,
and how to diagnose disagreements between codex-lb and Codex Desktop / the
Codex CLI quota pill.

## Why does codex-lb show my account as `rate_limited` when Codex Desktop says it's reset?

**Short answer:** Codex Desktop's *Settings → Account* UI and the
`/wham/usage` rate-limiter endpoint are two different OpenAI-side data
sources. They can legitimately disagree for a short window during a team
reset. codex-lb only reads `/wham/usage`.

### Which endpoint codex-lb uses

codex-lb refreshes account usage by calling

```
GET https://chatgpt.com/backend-api/wham/usage
```

per account on the configured refresh tick (default 60 s).
See [`app/core/clients/usage.py`](../../app/core/clients/usage.py) for
the client and [`app/modules/usage/service.py`](../../app/modules/usage/service.py)
for the scheduler.

### How the account status is derived

The fetched usage is fed through
[`apply_usage_quota`](../../app/core/usage/quota.py) which decides the
account status from the `primary_window.used_percent` value:

- `used_percent >= 100` and a secondary (quota) window is also full
  → `QUOTA_EXCEEDED`
- `used_percent >= 100` on the primary (rate-limit) window
  → `RATE_LIMITED`
- `used_percent < 100` → `ACTIVE` (auto-recovers on the next refresh tick
  that observes a sub-100 value)

There is no manual reset step inside codex-lb — recovery is purely
driven by what `/wham/usage` returns.

### Why Settings UI and `/wham/usage` can disagree

The Codex Desktop *Settings → Account* view and `/wham/usage` are fed by
**different OpenAI-side data sources**:

- `/wham/usage` exposes the rate-limiter's internal counter. It updates
  lazily — typically on the next chargeable request through that
  account, or when its internal window crosses `reset_at`.
- *Settings → Account* is fed by a separate account/quota view that
  often picks up team-side reset events earlier.

During a reset window it is therefore normal for *Settings → Account*
to show the reset state while `/wham/usage` still returns
`used_percent: 100` for a short period afterwards. codex-lb faithfully
mirrors `/wham/usage` during that window, so the account stays
`RATE_LIMITED` / `QUOTA_EXCEEDED` until upstream catches up.

### What you can do

- **Wait.** The next request through that account usually wakes the
  upstream rate-limiter; codex-lb auto-recovers on the next refresh
  tick after that.
- **Force a probe** (planned, tracked in
  [#677](https://github.com/Soju06/codex-lb/issues/677)). The dashboard
  will expose a per-account button that fires a single minimal
  `responses.create` against the affected account to nudge the upstream
  limiter to re-evaluate the window.

### How to verify it's upstream, not codex-lb

If you want to confirm the disagreement is on OpenAI's side and not in
codex-lb's mirror, call `/wham/usage` directly with the same account
token codex-lb is using:

```bash
ACCESS_TOKEN=...
ACCOUNT_ID=...   # chatgpt-account-id (UUID), not codex-lb's id

curl -s https://chatgpt.com/backend-api/wham/usage \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "chatgpt-account-id: ${ACCOUNT_ID}" \
  -H "Accept: application/json" | jq '.rate_limit'
```

If `primary_window.used_percent` is still `100` here while *Settings →
Account* shows the account as reset, then codex-lb has nothing to
mirror — you are inside the upstream propagation window and the only
fix is to wait (or, once #677 lands, hit the Probe button).

## Related

- [#676 — initial bug report on /wham/usage vs Settings UI divergence](https://github.com/Soju06/codex-lb/issues/676)
- [#677 — feat(dashboard): per-account force-probe action](https://github.com/Soju06/codex-lb/issues/677)
