# Context

## The incident this change is built from

2026-08-05, single-tenant deployment behind a Cloudflare tunnel. Codex CLI reported
`stream disconnected before completion: idle timeout waiting for SSE` repeatedly, while
the proxy's own error rate over the preceding three days was 13 failures in 1976
requests.

Log evidence at the time of the failure:

```
http_bridge_startup_wait_timeout stage=response_create_gate
  bridge_key=sha256:8721fd39e627 available=0 pending_count=1 queued_count=5
  pending_request_ids=d27c4984-… pending_request_ages_seconds=1548.9
```

`d27c4984` was submitted at 16:29:18Z. It logged `session_anchor_injected` and
`store_context_input_trimmed`, then nothing: no `response.created`, no terminal event,
no `request_logs` row (rows are written on completion). Meanwhile the same account
served `gpt-5.6-terra` and `claude-opus-5` traffic with 4–13s latencies throughout,
which rules out quota exhaustion, account health, and the network path to the proxy.

The five queued requests behind it were retrying gate acquisition every 10 seconds and
would have kept doing so until the 7200s request budget expired.

## Why the idle timeout did not help

`stream_idle_timeout_seconds` guards `_iter_sse_events`, which only runs once response
headers exist. The failing request never got that far, so the only applicable bound was
`http_responses_stream_request_budget_seconds` — also 7200s by default. Between the
`upstream_connect_timeout_seconds` handshake bound (8s) and the request budget (2h)
there was no bound at all.

Carrying the idle timeout into `sock_read` closes that hole without introducing a
fourth timeout for operators to reason about: the socket read that waits for response
headers is bounded by the same number that bounds every later read.

The resulting `aiohttp.SocketTimeoutError` is mapped to `StreamIdleTimeoutError` at the
stream boundary rather than being classified further down, because the generic
`aiohttp.ClientError` handler runs first and would otherwise report a silent
established connection as an unavailable upstream. The classifier's existing tie-break
between idle and request-budget expiry is deliberately left untouched.

## Why keepalive probes matter here

`keepalive_timeout=90` on the connector governs how long an *idle pooled* connection is
retained; it says nothing about whether that connection is still alive. Without
`SO_KEEPALIVE`, a socket dropped by a NAT or tunnel is only discovered when the
application writes and eventually times out. Enabling probes turns a silent black hole
into a transport error the existing failover already handles.

Probe tuning is deliberately best-effort. `TCP_KEEPIDLE` is Linux-only; macOS exposes
`TCP_KEEPALIVE` with different semantics; other platforms may expose neither. Failing
client construction over a missing socket option would trade a rare hang for a certain
outage.

## What this change deliberately leaves alone

The incident has a second half: the silent request held the per-session
`response_create_gate` while it waited. `main` already covers that through
`_http_bridge_pending_state_is_stale`, which uses `last_upstream_activity_at` as the
silence clock and retires a holder that stopped progressing. The deployment where this
was observed runs an older build without it.

Retirement is a cleanup of the symptom; this change removes the condition that produces
the symptom, so the two are complementary and stay in separate changes.

## Scope note

The unroutable-event logging is observability only. It was added because the incident
could not distinguish "upstream went silent" from "event arrived and was not routed to
its waiting request" — the bridge drops unmatched events without a trace today. If that
log ever fires in practice, the routing gap it exposes is a separate change.
