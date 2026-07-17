## Context

Uvicorn enables proxy-header handling by default. On a trusted socket peer it mutates the ASGI `client` and `scheme` before invoking the application, so code inside FastAPI cannot recover the transport peer from `request.client`. codex-lb already promises that `proxy_unauthenticated_client_cidrs` is evaluated against the raw socket peer, while other request consumers rely on Uvicorn's projected `request.client` and scheme.

The change must therefore preserve two identities without changing either policy: a private raw transport peer used only by the unauthenticated proxy-client allowlist, and Uvicorn's existing projected client/scheme used by current application behavior. It must cover both HTTP and WebSocket scopes and all owned server launch paths.

## Goals / Non-Goals

**Goals:**

- Capture the transport peer before any proxy-header projection and make it available for HTTP and WebSocket authentication.
- Preserve Uvicorn 0.47's current `X-Forwarded-For` and `X-Forwarded-Proto` behavior.
- Preserve the exact `FORWARDED_ALLOW_IPS` trust contract: unset defaults to `127.0.0.1`, an empty value trusts no peer, `*` trusts every peer, and other values retain Uvicorn's host/network parsing.
- Ensure every owned launcher performs proxy projection exactly once and after raw-peer capture.
- Fail closed for the unauthenticated allowlist when no preserved raw peer is available.

**Non-Goals:**

- Changing forwarded-header precedence, conflict/consensus rules, locality classification, or trusted-proxy configuration.
- Changing API firewall, request-log, bridge, drain, audit, dashboard-throttle, or other generic `request.client` consumers.
- Adding a new setting or replacing `FORWARDED_ALLOW_IPS` with a `CODEX_LB_*` setting.
- Supporting an external server command that performs proxy projection before loading the codex-lb application; such commands must disable server-level proxy headers as documented.

## Decisions

### Capture and projection share one outer application middleware

Add a focused ASGI middleware that records the incoming `scope["client"]` under a private codex-lb scope key for `http` and `websocket`, then delegates the same scope to Uvicorn's `ProxyHeadersMiddleware`. Register it last in `create_app()` so Starlette makes it the outermost user middleware.

Keeping capture and delegation in one middleware makes their ordering structural rather than relying on two independently registered middleware entries. Copying Uvicorn's parsing was rejected because it would create a second compatibility implementation for trusted hops, client ports, IPv6, and HTTP/WebSocket scheme conversion.

### Keep raw-peer access narrow and fail closed

Expose a small typed helper that reads the preserved peer from an `HTTPConnection`, which covers both `Request` and `WebSocket`. Only `_is_proxy_unauthenticated_socket_peer_allowed()` will use it. If the scope key is missing, the helper returns no peer and the allowlist does not match; it does not fall back to the possibly projected `request.client`.

Storing a second application-wide client identity or replacing `request.client` was rejected because other consumers intentionally depend on the projected value and are outside this change.

### Disable server-level projection in owned launch paths

Set `proxy_headers=False` in `app.cli` and add `--no-proxy-headers` to the direct Docker Compose command and direct FastAPI/Uvicorn documentation. Docker, distroless, production Compose, and Helm paths already delegate to `app.cli` and inherit the setting.

Leaving Uvicorn's outer middleware enabled was rejected because the application would capture an already projected identity. Removing proxy projection entirely was rejected because it would change existing `request.client`, URL, and WebSocket scheme behavior.

### Read the existing trust environment without reinterpretation

Construct Uvicorn's application middleware with `os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1")`. Passing the raw string preserves Uvicorn's handling of empty strings, wildcards, comma-separated hosts, literals, addresses, and networks. No validation or normalization layer is added.

## Risks / Trade-offs

- **An external launch command omits `--no-proxy-headers` and projects too early** → Document the required flag on direct commands and add launcher contract tests; the allowlist still fails closed when capture is absent, but it cannot distinguish an already rewritten peer.
- **Middleware registration order changes later** → Add a create-app/middleware regression that proves raw capture precedes projection and projected client/scheme remain visible downstream.
- **Uvicorn changes proxy-header semantics in a future upgrade** → Delegate to Uvicorn's middleware and test codex-lb's required compatibility cases instead of maintaining a fork.
- **A non-owned ASGI integration calls auth outside the configured app** → Missing raw-peer state never satisfies `proxy_unauthenticated_client_cidrs`.

## Migration Plan

No data migration is required. Ship the application middleware and owned launcher changes together so projection moves atomically from the server wrapper into the application. Rollback is the reverse code deployment; no persisted state or configuration conversion is involved.

## Open Questions

None.
