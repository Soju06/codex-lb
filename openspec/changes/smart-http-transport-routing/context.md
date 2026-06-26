# Context — Smart HTTP Downstream Transport Routing

The default policy is `smart` because downstream HTTP callers are commonly
single-shot requests where opening an upstream WebSocket adds handshake and
admission cost before the first token without preserving useful state.
Operational evidence for overload-before-first-token failures showed that
this cost dominates the failure mode for non-sticky HTTP requests.

Sticky requests are different. A request with `previous_response_id`, a
client-supplied prompt cache key, a session header, or a turn-state header is
already expressing continuation intent. For those requests, upstream
WebSocket setup can be amortized across multi-turn work and preserves the
existing sticky routing behavior.

The routing split is therefore:

- single-shot downstream HTTP under `smart`: use upstream HTTP;
- sticky downstream HTTP under `smart`: keep upstream WebSocket when the base
  transport resolved there;
- native downstream WebSocket clients: leave the path unchanged;
- explicit operator overrides, oversized payload bypass, and image bypasses:
  keep their higher precedence.
