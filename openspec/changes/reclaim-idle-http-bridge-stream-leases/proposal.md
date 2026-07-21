## Why

HTTP bridge sessions retain their per-account stream lease for the full
session lifetime so they can be reused without reacquiring account capacity.
Under disconnect-heavy traffic, an otherwise idle local session can therefore
hold the last stream slot while new HTTP Responses requests fail with
`account_stream_cap`. The durable bridge row can already be gone while the
owner replica still retains the in-memory session and lease, so waiting for
database cleanup does not restore local capacity.

## What Changes

- When HTTP bridge session creation receives a local `account_stream_cap`,
  inspect the local bridge registry for an eligible idle session that still
  holds a stream lease.
- Detach at most one idle session from the exact selector-reported set of
  otherwise eligible, capacity-blocked accounts, then close it through the
  existing bounded cleanup path before retrying account selection.
- Preserve active work, admission handoffs, pre-submit reservations, API-key
  account scopes, and required preferred-account continuity.
- Prefer reclaiming an idle session on the requested preferred account when
  that preference is soft, while retaining the existing bridge eviction order.

## Impact

- Local stream slots held only by idle reusable HTTP bridge sessions can be
  recovered immediately under real account-cap pressure instead of waiting for
  the lease TTL or a process restart.
- Active and pre-submit bridge sessions remain protected.
- No setting, schema, endpoint, or response-format change is introduced.
- An idle bridge may lose warm-session or prompt-cache reuse when its stream
  lease is needed for new work; this happens only after account selection has
  already reported `account_stream_cap`.

Related to #1354
