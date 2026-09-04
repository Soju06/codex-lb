## Why

When an account is deactivated, requires re-authentication, changes proxy binding, or is deleted, the service detaches every durable HTTP-bridge row of that account: the row becomes `CLOSED`, its owner account, lease and continuity anchors are cleared, and its aliases are deleted. The durable request lookup still returned such rows by canonical key, and hard-affinity continuations (Codex `thread_header` / `session_header`) treat a durable row without an owner account as a missing required owner. Every existing Codex thread of an invalidated account therefore failed closed with `previous_response_owner_unavailable` ("retry later") on every retry, even after the account was reactivated, while a fresh thread or side chat on the same client worked.

On 2026-09-03 an upstream outage deactivated every account for ~40 minutes and detached 670 bridge rows; after reactivation every pre-outage Codex thread stayed permanently broken. The rows are never purged by closed-row cleanup because they still own operation transcripts.

## What Changes

- Durable request-target lookup MUST NOT report a detached row (CLOSED, no owner account, no owner instance, no continuity anchors) as a lookup hit, by canonical key or by alias.
- A hard-affinity request whose only durable evidence is a detached row proceeds as a fresh request and its claim re-owns the same canonical row.
- A `CLOSED` row that still names its owner account (ordinary release) keeps its continuity value and is unchanged.
- Add regression coverage for both cases using the real account-invalidation detach.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: Add the requirement that detached durable bridge rows are not continuity owner evidence.

## Impact

`app/modules/proxy/durable_bridge_coordinator.py` (`lookup_request_targets`) and its unit tests. No API, schema, settings, or dashboard change. Rows that still name an account, live/draining rows, and every conflict rule between independent hard sources are unchanged.
