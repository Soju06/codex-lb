## Context

The secondary history already contains persisted usage observations;
`UsageHistory.used_percent` is non-nullable, while `window_minutes` is optional.
Missing duration therefore does not mean that no secondary usage was reported.

## Decision

Use the presence of a fresh secondary history observation to invalidate a
Pro Max fleet forecast. Keep the existing account-status and freshness checks.
Do not infer a seven-day duration or an absolute allowance to make the sample
eligible for suppression.

For example, a known Pro weekly window plus a fresh Pro Max secondary sample
at 20% used and no duration must yield `weeklyCreditPace: null` from both
dashboard endpoints, rather than a forecast covering only Pro.

## Verification

Extend the existing unit and route regressions with absent and zero durations,
then verify both endpoints through a synthetic local HTTP server. Existing
complete-window, missing-reset and known-plan tests remain in place.
