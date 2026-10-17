## Context

The outermost application middleware preserves the server-observed socket peer
and then applies Uvicorn-compatible forwarding projection. Dashboard
trusted-header sanitization and principal attribution currently inspect the
projected `scope["client"]`, so an allowed forwarding header can replace the
provenance that those checks are intended to authorize.

The earlier proxy-identity hardening deliberately limited raw-peer use to
locality and unauthenticated proxy access. Trusted-header principal attribution
therefore needs a separate, narrow follow-up.

## Goals / Non-Goals

**Goals:**

- Authorize the configured dashboard identity header only from a raw socket
  peer inside `firewall_trusted_proxy_cidrs`.
- Fail closed when raw-peer capture is unavailable or proxy-header trust is
  disabled.
- Preserve forwarding projection for downstream consumers.
- Prove both accepted trusted-proxy traffic and rejected direct spoofing
  through the complete application middleware stack.

**Non-Goals:**

- Change forwarded-client resolution, Uvicorn projection, or trusted proxy CIDR
  configuration.
- Change API firewall, locality, logging, bridge, WebSocket, or dashboard UI
  behavior.
- Add a setting, dependency, migration, or compatibility fallback.

## Decisions

1. Both the identity-header sanitizer and trusted-header authentication read
   the peer through `raw_socket_peer_host`. Using the existing preserved value
   keeps the trust decision tied to transport provenance while leaving
   `request.client` projected for its established consumers.
2. The sanitizer removes the configured identity header unless raw provenance
   is present, proxy-header trust is enabled, and the raw peer is trusted.
   Trusted-header authentication repeats the same authorization before
   constructing a principal. Keeping both checks prevents a future middleware
   composition change or direct auth call from turning sanitization into the
   sole security boundary.
3. Regression tests send real HTTP requests through `create_app()` with
   explicit ASGI transport peers. They configure forwarding projection for all
   peers so an untrusted direct peer can reproduce the vulnerable projected
   identity before the fix.

## Risks / Trade-offs

- **A custom embedding omits raw-peer capture** → trusted-header authentication
  fails closed instead of accepting an unverifiable identity.
- **An operator trusted only the projected address rather than the transport
  proxy** → the request is denied until the actual proxy peer is included in
  the configured trusted CIDRs; this is the intended trust model.
- **The sanitizer and auth check duplicate one predicate** → the small
  duplication is retained as defense in depth at two distinct security
  boundaries.
