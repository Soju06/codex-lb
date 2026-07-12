## Design

New limit rules are initialized in the API key service during update. For each submitted rule without an existing `(limit_type, limit_window, model_filter)` match and without `resetUsage`, the service asks the repository to aggregate usage for the key inside the new rule's active window.

The lookback window is derived from the same duration used for the new limit:

- `reset_at = next_limit_reset(now, limit_window)`
- `since = now - limit_window_delta(limit_window)`
- `until = now`

The repository computes usage from `request_logs` scoped to the API key, the time window, and the optional model filter. Token limits use token columns; cost limits convert each `cost_usd` row to truncated integer microdollars before summing, matching live cost-limit accrual. Credit limits are not derived from request logs and remain zero.

Existing limit rows keep their current value, preserving the established update behavior. `resetUsage=true` still forces all submitted limit rows to zero.
