# Add account inflight lease metrics

## Why

Operators can currently see cumulative account lease acquisitions, releases, stale reclaims, and cap rejections, but not the live number of leases consuming each account-local cap. During stream-cap saturation this makes it hard to distinguish "all active streams are legitimately busy" from "leases leaked or stale state is holding the account unavailable."

## What Changes

- Add a Prometheus gauge for current in-process account leases by account and lease kind.
- Update the gauge whenever the load balancer acquires, releases, or stale-reclaims an account lease.
- Keep the metric labels bounded to account id and lease kind, and forbid request/session identifiers or credentials in labels.

## Impact

- Affects proxy-runtime observability only.
- Does not change account selection or cap enforcement behavior.
- Helps operators see active `stream` and `response_create` pressure before and during `account_stream_cap` or `account_response_create_cap` rejections.
