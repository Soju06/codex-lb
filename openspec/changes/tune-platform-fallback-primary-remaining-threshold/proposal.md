## Why

Operators watch the dashboard's primary and secondary remaining percentages when deciding whether ChatGPT-web capacity is running low. The current Platform fallback policy is expressed only through the generic drain thresholds, which makes the public-route fallback point less obvious during manual validation.

## What Changes

- Define dedicated Platform fallback rules for the primary and secondary windows using the dashboard-visible remaining percentages.
- Start Platform fallback eligibility when the compatible ChatGPT-web pool has no candidates with `primary_remaining_percent > 10`.
- Treat a compatible ChatGPT-web candidate as healthy only while it has both `primary_remaining_percent > 10` and `secondary_remaining_percent > 5`.
- Keep the existing phase-1 route restrictions unchanged.

## Impact

- Public HTTP fallback will activate only when the compatible ChatGPT-web pool is nearly exhausted in both windows, without changing ChatGPT-private routing.
- Operators can validate fallback activation against the primary and secondary remaining percentages they already see in the UI.
