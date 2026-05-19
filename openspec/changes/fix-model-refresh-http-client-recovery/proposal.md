## Why

Model registry refresh uses the shared outbound `aiohttp` client to call the upstream models endpoint and, when needed, to refresh account tokens first. In low-reliability network environments (VPN transitions, laptop sleep/wake, DNS/socket churn), the refresh path can fail before an HTTP response is received. Before this change, those transport failures were treated like ordinary fetch failures, so the process could keep reusing a stale shared client/connector until restart.

A recovery path that rebuilds the shared client also has to preserve in-flight proxy work. If the old client is closed immediately while streams, compact calls, transcriptions, usage fetches, or file upload-protocol calls are still using it, the recovery action can interrupt unrelated requests.

## What Changes

- Mark model-fetch and token-refresh transport exceptions distinctly from HTTP status, auth, and invalid-response failures.
- On the first transport error in a model-refresh failover cycle, rotate the shared HTTP client and retry the failed refresh/fetch operation once.
- Manage the shared HTTP client behind leases: default shared-session/retry-client callers lease the client for the full duration of their upstream operation, and retired clients close only after active leases drain.
- Keep shutdown bounded by allowing global close to force-close active/retired clients instead of waiting forever on long-lived streams.

## Impact

- Model registry refresh can recover from transient stale-socket/connector failures without requiring a process restart.
- In-flight proxy and auxiliary upstream calls are not torn down by model-refresh client rotation as long as they use the shared client through the lease helpers.
- Non-transport upstream failures keep the existing behavior: HTTP errors, invalid responses, and permanent refresh failures do not trigger shared-client rotation.
