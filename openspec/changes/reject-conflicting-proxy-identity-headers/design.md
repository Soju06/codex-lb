## Context

Locality trusts proxy headers only when the transport peer belongs to `firewall_trusted_proxy_cidrs`. Fixed cross-family precedence lets a preseeded family win over conflicting proxy evidence. Separately, Uvicorn's server middleware can project `X-Forwarded-For` into `scope["client"]` before the application sees the transport peer.

## Decisions

1. **Own capture before projection.** Project launchers disable server-level proxy handling. The outermost application middleware stores the incoming HTTP/WebSocket peer, then delegates once to Uvicorn's `ProxyHeadersMiddleware`. Downstream consumers still see Uvicorn's projected client and scheme.
2. **Use raw provenance narrowly.** Only locality and `proxy_unauthenticated_client_cidrs` read the preserved peer. Missing capture fails closed. Trusted-header principal attribution, the API firewall, request logs, bridge metadata, drain, and audit remain unchanged; other `request.client` consumers stay projected.
3. **Make consensus a locality opt-in.** Every allowed family containing a non-whitespace value is resolved with its existing family rules. All results must be valid and IP-equivalent. Empty-only families are ignored; singleton duplicates and chain fields keep their current behavior. Generic resolver callers retain precedence.
## Risks / Trade-offs

Conflicting or malformed populated families now deny local/disabled-auth access. This is intentional fail-closed behavior. Redundant Cloudflare/nginx-style families continue to work when they resolve to the same IP.
