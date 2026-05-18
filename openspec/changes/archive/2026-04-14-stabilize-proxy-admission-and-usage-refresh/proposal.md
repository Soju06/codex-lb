# Stabilize Proxy Admission And Usage Refresh

## Why

The proxy currently mixes short-lived HTTP requests and long-lived websocket sessions into one downstream bulkhead, so active websocket sessions can starve `/backend-api/codex/responses` and `/backend-api/codex/responses/compact` before those requests ever reach the proxy handlers. Those local rejections are hard to distinguish from upstream failures because they return generic overload responses and websocket handshake denials appear as `403` in the access log.

The proxy also lacks a second-stage admission controller around the most expensive upstream work. Concurrent token refreshes, upstream websocket connects, and first-turn response creation can amplify overload and retry storms even after downstream admission succeeds.

On the usage side, background refresh keeps retrying some accounts that return usage `401` or `403` indefinitely. When upstream explicitly says the account has been deactivated, the account should be deactivated locally. When the error is transient or ambiguous, the scheduler should back off instead of hammering the same account every cycle.

## What Changes

- Split downstream proxy admission into separate HTTP, websocket-session, and compact-request lanes.
- Return explicit local-overload error payloads and `Retry-After` headers for admission rejections, including websocket handshake denials.
- Add second-stage admission controls around token refresh, upstream websocket connect, and first-turn response creation.
- Reserve a compact lane so `/responses/compact` stays available during heavy chat traffic.
- Reduce retry amplification by singleflighting token refreshes and short-circuiting rapid repeat failures.
- Add usage-refresh cooldown for repeated auth-like failures and treat deactivation-signaling `401` messages as permanent account deactivation.
- Emit operator-visible logs for local admission rejections with the rejection stage and capacity lane.

## Capabilities

### New Capabilities

- `proxy-admission-control`: define downstream and expensive-work admission policies, overload responses, and compact protection.
- `usage-refresh-policy`: define cooldown and deactivation behavior for background usage refresh failures.

### Modified Capabilities

- `proxy-runtime-observability`: log local admission rejections with explicit rejection metadata.

## Impact

- Code: `app/core/config/settings.py`, `app/core/resilience/bulkhead.py`, `app/main.py`, `app/modules/accounts/auth_manager.py`, `app/modules/proxy/service.py`, `app/modules/usage/updater.py`
- Tests: bulkhead middleware tests, proxy service unit tests, auth manager tests, usage updater tests, settings tests
