# Outbound HTTP Clients Context

## Purpose and Scope

This capability owns shared outbound HTTP lifecycle, proxy-aware WebSocket egress, and the boundary between local transport failures and account-specific upstream failures. The normative contract is in `openspec/specs/outbound-http-clients/spec.md`.

The shared-egress correlation described here is intentionally narrow. It protects account health when one local proxy or direct network path drops several Responses WebSockets at once; it does not change the outcome of the interrupted requests.

## Decision Rationale

Typed DNS and route errors already provide strong process-network provenance. A bare WebSocket EOF is different: by itself it may be an account-specific upstream failure, but several nearly simultaneous EOFs from distinct accounts on the same concrete egress are strong evidence that the shared path failed.

The receive adapter therefore holds an ambiguous no-close Responses failure for up to one second. A second distinct account on the same egress changes every waiting candidate in that incident to the stable `proxy_network_unavailable` classification before account-health settlement. A short retained history gives trailing failures the same classification. One account cannot manufacture the threshold by opening several concurrent requests.

Concrete egress keys come from structured connection state rather than exception text:

- routed connections use the actual endpoint id returned after proxy-route fallback;
- environment proxies use parsed scheme, hostname, and port;
- direct connections use the parsed destination scheme, hostname, and port.

Usernames, passwords, URL paths, access tokens, and raw exception messages are not part of an egress key. Correlation of an ordinary EOF also does not rotate the shared HTTP client because it has not identified a failed shared-client generation.

## Constraints and Non-Goals

- Correlation is process-local and bounded; it adds no setting, database state, migration, or cross-replica protocol.
- Only Responses receive failures without a complete peer close frame participate. Live sideband sockets retain their close semantics.
- An already typed process-network error keeps its classification immediately and follows existing transport-rotation behavior.
- Correlation happens after request dispatch. It does not prove whether upstream accepted the request, so it does not authorize replay, account switching, or continuity-owner movement.
- Account ids must be non-empty and distinct. Anonymous failures remain on the established path.

## Failure Modes and Edge Cases

- Repeated failures from one broken account time out of the correlation window and retain normal transient health penalties.
- Failures through different routed endpoints, environment proxies, or direct destinations do not corroborate one another.
- A received close frame remains authoritative even when another account closes nearby.
- A downstream keepalive tick does not cancel or duplicate an in-progress receive classification; the relay owns and cleans up one persistent receive task.
- A request-budget, stream-idle, or eventless-response deadline can expire while that persistent task is already classifying an observed EOF. The task stays owned until its bounded decision completes, so the deadline cannot replace a correlated network result with a timeout settlement and account-health write. Truly silent receives that have not observed a failure still obey their normal deadline.
- Cancellation detaches the calling loop's waiter while retaining only bounded incident evidence; expired observations are ignored and removed lazily on subsequent observations.
- Capacity pressure evicts the oldest correlation evidence but does not release that caller before its own bounded judgment window ends.

## Concrete Incident Example

At 19:32:10, one environment-proxy EOF ended seven Responses WebSockets across four upstream accounts within 358 milliseconds. One still-valid owner account had three concurrent requests, so three independent `stream_incomplete` health writes crossed its transient-error threshold. Continuity-bound requests then failed with `previous_response_owner_unavailable` until the short local backoff expired, even though the account remained active and had valid credentials and quota.

With bounded correlation, those seven interrupted requests still fail and are not replayed. Their adapters report `proxy_network_unavailable`, so none of the four accounts receives an error-health or circuit-breaker write and continuity remains pinned to the existing owners.

For example, if account A observes the shared EOF just before its request budget expires, its relay waits only for the already-started bounded classification. If account B corroborates the same egress during that window, A settles as `proxy_network_unavailable`; the expired budget does not overwrite that transport evidence.

## Operational Notes

During rollout, compare clusters of `proxy_network_unavailable` request failures across distinct accounts with account-health counters. A same-egress incident should leave those counters unchanged. A lone `stream_incomplete`, a named close code, or repeated failures from one account should still produce the existing account-specific signal.

Rollback requires no data conversion because detector observations live only in process memory. Related Responses settlement and continuity behavior is documented in `openspec/specs/responses-api-compat/`.
