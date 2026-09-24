## Why

Auth Guardian currently maintains a second credential-age policy alongside the
canonical request-time refresh policy. Request paths use `should_refresh()` and
treat credentials as fresh for eight days, while the default-on guardian scans
every six hours and force-refreshes active and paused accounts after only twelve
hours. In steady state the guardian therefore rotates every account roughly
sixteen times more often than the policy used everywhere else.

The shorter guardian-only threshold has no documented upstream lifetime or
incident evidence behind it. It also cannot be tuned independently: the only
operator choice is to disable Auth Guardian entirely, losing the useful
protection for genuinely idle and paused accounts. The simpler and safer model
is one proactive-refresh policy shared by request traffic and background
maintenance.

## What Changes

- Auth Guardian candidate selection and its fresh per-account recheck use the
  existing `app.core.auth.refresh.should_refresh()` predicate.
- The guardian-only 12-hour constant, scheduler field, and selection parameter
  are removed. The six-hour polling cadence remains and determines the next
  eligibility scan after the canonical eight-day window becomes due; the fixed
  100-account batch and active failure backoff still govern admission.
- Active and paused account eligibility, leader gating, cross-replica refresh
  claims, and failure backoff remain unchanged. After the fresh row passes the
  shared predicate, the worker keeps `force=True` only as execution of that
  admitted refresh—not as a separate freshness policy.
- The dashboard description and stable documentation stop promising a 12-hour
  refresh and describe the shared eight-day policy.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Auth Guardian must use the same canonical credential
  freshness predicate as request-time proactive refresh and must not carry a
  shorter background-only threshold.

## Impact

- Code: `app/core/auth/guardian.py`.
- Tests: guardian unit and scheduler integration construction.
- Docs/UI: the usage-refresh specification, deployment context, and translated
  Auth Guardian dashboard description.
- No schema or API change. Accounts still refresh immediately after an upstream
  401; only unnecessary background exchanges between the twelve-hour and
  eight-day marks are removed.
