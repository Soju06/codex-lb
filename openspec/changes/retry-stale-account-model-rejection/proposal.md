# Retry stale per-account model rejections

The model registry can briefly be less current than the upstream account
entitlement it represents. During that window, selection can route a model to
an account whose last-known catalog advertised it, only for upstream to reject
the request before `response.created` with the account/model unsupported
envelope. The request is safe to move at that point, but the proxy currently
surfaces the transient routing race (or rewrites it to a generic bridge
failure).

Add one narrowly classified, pre-acceptance failover attempt across native
Responses WebSocket, the HTTP responses bridge, and raw HTTP/SSE. The rejected
account is excluded only for that request and is not penalized globally. Hard
account ownership and uploaded-file pins remain fail-closed, and the original
upstream 400 is preserved when no compatible replacement account is available.
