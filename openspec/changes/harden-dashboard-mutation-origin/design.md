## Context

Dashboard requests reach FastAPI through a stack of pure-ASGI middlewares registered in `app/main.py`. Authentication is a route dependency (`validate_dashboard_session`), so anything that must apply to every mutation, including routes that issue sessions and therefore have no session gate (`/api/dashboard-auth/*`), has to run earlier than routing. The session cookie is `SameSite=Lax`; the loopback no-password mode authenticates with no credential. The React client keeps auth state in a zustand store whose initial values are admin-shaped and whose gate only shows a spinner while `loading` is true, which is false on the very first render.

## Goals / Non-Goals

**Goals**

- Every non-safe `/api/` request that a browser marks as cross-site is refused before any handler or dependency runs, with the dashboard error envelope.
- No configuration: the expected origin is derived from the request. No new `CODEX_LB_*` setting, no origin allowlist.
- Non-browser clients and the existing test suites (which send neither `Origin` nor `Sec-Fetch-Site`) keep working.
- The client never renders admin controls, or a read-only frame for an admin, before the session response arrives.

**Non-Goals**

- CSRF tokens or double-submit cookies. Fetch metadata plus `Origin` is sufficient for a same-origin SPA and needs no client change.
- CORS. The dashboard has no cross-origin consumers; adding CORS would widen the surface this change narrows.
- Hiding navigation items or changing page layouts for guests (a separate change).
- Protecting `/v1`, `/backend-api`, `/internal`: those are bearer or loopback authenticated data-plane surfaces and are outside the dashboard cookie's reach.

## Decisions

### Middleware, not a dependency

A `Depends` on every router would miss the `dashboard_auth` router, which intentionally has no session dependency but hosts the most sensitive mutations (password change, TOTP disable, logout). A middleware registered right after the dashboard auth proxy sanitizer sees every request before routing and cannot be forgotten by a new router. The route matrix test walks the live route table and fires a cross-site request at every mutation, so a future exemption has to be explicit.

### `Sec-Fetch-Site` first, `Origin` second, absence allows

`Sec-Fetch-Site` is set by the browser, cannot be set by page script, and directly answers the question ("same-origin" / "none" pass, everything else fails). Browsers only send it to potentially trustworthy destinations (https, localhost), so a dashboard served over plain http on a LAN address receives `Origin` but not `Sec-Fetch-Site`; the `Origin` comparison covers that case. When neither header is present the request did not come from a browser page (browsers always attach `Origin` to cross-site non-GET requests), so it passes. `Origin: null` is rejected: it appears for sandboxed frames and privacy-redacted cross-site requests, never for the dashboard's own SPA.

Alternative rejected: requiring a header. That would break curl/httpx callers and the in-process test transports, for no security gain against the threat (a foreign page in the operator's browser).

### Expected origin is derived from the request

The comparison target is `scope["scheme"]` plus the `Host` header, with default ports (80/443) normalized and the host compared case-insensitively. This is the same information Starlette uses for `request.url`, so the check agrees with how the app already sees itself, including behind a trusted reverse proxy that sets `X-Forwarded-Proto` (`FORWARDED_ALLOW_IPS`). A configured public origin was rejected: it would be a new setting that most installs would have to discover after a confusing 403, and `Sec-Fetch-Site` already handles the https case where proxies are common.

Consequence documented in the proposal: a reverse proxy that rewrites `Host` to the upstream address must forward the browser's `Host` including its port (nginx `$http_host`, Apache `ProxyPreserveHost On`). The Vite dev proxy is configured with `changeOrigin: false` for the same reason: the string shorthand rewrites `Host` to the backend target and would 403 every mutation when the dev server is opened on a LAN address over plain http. A mismatch produces a visible 403 with a specific code rather than a silent bypass.

### Exemptions are path prefixes plus bearer presence

`/api/fleet/` and `/api/codex/` authenticate with a proxy API key, never a cookie, and are called by non-browser clients that may legitimately set `Origin`. A request that carries `Authorization: Bearer ...` is also exempt regardless of path because a foreign page cannot attach that header without a CORS preflight the server never grants. The scheme comparison is case-insensitive and requires a non-empty credential so a bare `Authorization: Bearer` does not disable the check.

### Client starts least-privilege and the gate holds

`role: "guest"`, `permissions: []`, `canWrite: false` is the minimum the backend can grant. `role` stays non-nullable so no consumer needs a null branch; existing `role === "admin"` comparisons fail closed. `logout()` resets to the same values before refreshing so the pre-refresh window is not admin-shaped. The gate now waits on `initialized` alone: the first commit is the spinner, `refreshSession()` runs, and the application renders once with real state. Rendering the app early and letting pages guard themselves was rejected because it depends on every page remembering to check `initialized`.

### Schema accepts any permission string

`permissions: z.array(z.string())` lets later changes emit fine-grained values (`accounts:export`, scope suffixes) without a coordinated frontend release; `canWrite` is still `permissions.includes("write")`. `DashboardPermissionSchema` stays exported for code that wants the two known coarse values.

## Risks / Trade-offs

- [Risk] A reverse proxy rewrites `Host` and the dashboard is served over http, so `Origin` never matches. → Visible `403 cross_site_request_rejected` on the first mutation, documented in `docs/authentication.md` with the fix; https deployments are covered by `Sec-Fetch-Site` regardless of `Host`.
- [Risk] An operator script posts to `/api/*` with an `Origin` header for some reason. → It must match the target origin; scripts that send no `Origin` are unaffected.
- [Risk] Admins see the spinner slightly longer on first load. → Replaces a flash of read-only UI plus a re-render; the spinner was already shown after the first effect.
