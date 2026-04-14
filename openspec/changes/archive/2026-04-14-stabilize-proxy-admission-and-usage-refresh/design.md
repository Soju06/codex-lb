## Context

This incident exposed two different problems:

1. Downstream admission fairness: one shared proxy semaphore treats long-lived websockets and short HTTP requests the same, so websocket occupancy can starve compact and HTTP request paths.
2. Upstream expensive-work amplification: even after a request clears downstream admission, it can still pile up on token refresh, upstream websocket connect, or first-turn creation.

The usage scheduler has a related resilience gap: repeated usage `401` or `403` failures keep retrying without either deactivating clearly dead accounts or backing off ambiguous failures.

## Goals / Non-Goals

**Goals:**
- Prevent websocket sessions from starving HTTP proxy traffic.
- Preserve a compact lane under heavy mixed traffic.
- Surface local overload clearly in both HTTP and websocket handshake flows.
- Bound concurrency for token refresh, websocket connect, and response-create admission.
- Reduce refresh retry storms with per-account singleflight and short failure caching.
- Deactivate accounts on clear usage deactivation signals and cool down repeated ambiguous auth failures.

**Non-Goals:**
- Introduce a distributed admission controller across replicas.
- Redesign account-selection strategy or upstream failover policy.
- Persist usage-refresh cooldown state in the database.

## Decisions

### Split downstream proxy admission by traffic class

The middleware bulkhead will expose separate semaphores for:

- proxy HTTP requests
- proxy websocket sessions
- compact HTTP requests
- dashboard/API traffic

Compact requests get their own lane instead of sharing the general HTTP pool so short bootstrap requests remain available during websocket-heavy traffic.

### Deny websocket handshakes with HTTP responses, not close frames

When local admission rejects a websocket handshake, the middleware will return an HTTP denial response with the real overload status and OpenAI-style error payload. This removes the current ambiguous `403` access-log signal caused by pre-accept websocket closes.

### Add a second-stage work admission controller

`ProxyService` will own an in-process controller that separately limits:

- token refresh work
- upstream websocket connect work
- first-turn response creation

The controller is global to the process, not per account. This is a deliberate first step: it bounds the most expensive shared work without introducing cross-replica coordination or account-sharing bugs.

### Reuse existing first-turn lifecycle hooks for response-create admission

The websocket and HTTP bridge flows already track `awaiting_response_created` and release a per-session gate when `response.created` arrives or the request fails. The new response-create admission slot will piggyback on that lifecycle so permits release on the same terminal paths.

### Singleflight forced refreshes with a short failure TTL

Per-account refresh attempts will share one in-flight refresh task. If that refresh fails, subsequent callers within a short cooldown window will reuse the failure instead of immediately reissuing another refresh request upstream.

### Cool down ambiguous usage auth failures, but deactivate clear deactivation signals

Background usage refresh will:

- deactivate on `402` and `404` as before
- deactivate on `401` when the upstream message clearly indicates the OpenAI account has been deactivated
- apply an in-memory cooldown for repeated `401` and `403` usage failures that are not clear deactivation signals

This avoids repeated scheduler noise without deactivating accounts on generic auth glitches.

## Risks / Trade-offs

- Separate downstream lanes can increase total in-flight work if operators keep all limits high. Mitigation: default websocket and compact lanes remain bounded and independently configurable.
- In-process work admission does not coordinate across replicas. Mitigation: this still protects a single pod from self-induced overload and is a clean foundation for later distributed coordination if needed.
- Usage cooldown is memory-only, so a restart forgets the backoff state. Mitigation: that is acceptable for scheduler hygiene and avoids a schema change.

## Migration Plan

1. Add new settings with backward-compatible defaults derived from the existing proxy bulkhead limit.
2. Deploy downstream lane splitting and explicit overload responses first.
3. Deploy second-stage work admission and refresh singleflight/failure TTL.
4. Deploy usage-refresh cooldown and deactivation-signal handling.
5. Verify with targeted middleware, proxy, and usage tests plus `openspec validate --specs`.

## Open Questions

- None.
