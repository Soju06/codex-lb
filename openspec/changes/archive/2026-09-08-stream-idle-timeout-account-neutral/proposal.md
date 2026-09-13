# Why

An HTTP SSE Responses stream whose first upstream event is `response.failed`
with `stream_idle_timeout` still failovers, but `_handle_stream_error` treats
the code as a transient account fault and calls `record_error`. Idle silence
is not evidence that the account is unhealthy. The penalty can backoff a
healthy account and send later requests into probe/drain.

# What Changes

- Treat `stream_idle_timeout` as an account-neutral error code.
- Keep this-request exclude/failover so the idle attempt does not retry the
  same account.
- Do not write `record_error`, rate-limit, quota, or permanent-failure health
  for that idle timeout.

# Capabilities

### Modified Capabilities

- `responses-api-compat`: HTTP SSE first-event `stream_idle_timeout` must stay
  account-neutral for health writes.

# Impact

Existing failover and request-log behavior stay the same. Websocket liveness
and keepalive timeouts already have their own account-neutral rules.
