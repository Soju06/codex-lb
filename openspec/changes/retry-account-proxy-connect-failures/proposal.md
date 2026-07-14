# Retry confirmed account-proxy connect failures

An account-bound upstream proxy can remain administratively active after its
listener stops accepting connections. Responses requests selected onto that
account then fail before upstream sees the request, but the proxy currently
turns the transport failure into a terminal stream event or a bridge startup
error. Client retries may select the same account again.

Add explicit, sanitized dispatch provenance to account-routed transport
failures. When the transport library proves that the connection to the
selected proxy failed before request dispatch, try another endpoint in the
same proxy pool and then another eligible account for movable Responses
requests. Apply bounded transient account backoff so independent requests do
not immediately rediscover the dead route. Ambiguous failures and hard
continuity or file ownership remain non-replayable and fail closed.
