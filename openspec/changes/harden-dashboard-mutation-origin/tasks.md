## 1. Cross-site request rejection (backend)

- [x] 1.1 Add `app/core/middleware/dashboard_csrf.py`: pure-ASGI middleware applying the `Sec-Fetch-Site` / `Origin` rules to non-safe `/api/` requests without a bearer token, exempting `/api/fleet/` and `/api/codex/`, answering `403 cross_site_request_rejected` in the dashboard envelope, logging method/path/reason only.
- [x] 1.2 Export `add_dashboard_csrf_middleware` from `app/core/middleware/__init__.py` and register it in `app/main.py` right after the dashboard auth proxy sanitizer.

- [x] 1.3 Log rejections with both sides of the comparison (`origin=<scheme>://<host>:<port> expected=<scheme>://<host>:<port>`, `-` when unparsable) and nothing else from the headers.
- [x] 1.4 Configure the Vite dev proxy with `changeOrigin: false` so the browser `Host` reaches the backend on LAN dev servers.

## 2. Least-privilege client boot (frontend)

- [x] 2.1 `use-auth.ts`: initial state and `logout()` reset use `role: "guest"`, `permissions: []`, `canWrite: false`; `permissions` typed as `string[]`.
- [x] 2.2 `auth-gate.tsx`: render the spinner whenever `initialized` is false.
- [x] 2.3 `schemas.ts`: `role` defaults to `guest`, `permissions` is `z.array(z.string())` defaulting to `[]`.

## 3. Verification

- [x] 3.1 Unit tests for the middleware: fetch-site allow/reject, origin match with default-port and case normalization, origin mismatch, `Origin: null`, no headers, safe methods, non-`/api/` paths, bearer prefixes and header, case-insensitive header names, websocket/lifespan scopes untouched, log content.
- [x] 3.2 Integration tests on the real app: cross-site `POST /api/dashboard-auth/logout` and `PUT /api/settings` rejected before authentication; same-origin and matching-origin requests reach the handlers; reads and `/api/fleet/` unaffected.
- [x] 3.3 Route matrix: every non-safe `/api/` route outside the bearer prefixes rejects `Sec-Fetch-Site: cross-site` with `cross_site_request_rejected`.
- [x] 3.4 Frontend tests: store starts least-privilege, `logout()` does not restore admin defaults, schema defaults and tolerant permission parsing, gate hides children until initialized, accounts page test seeds an admin session.
- [x] 3.6 Route matrix sibling: every non-safe, non-exempt `/api/` route accepts `Sec-Fetch-Site: same-origin` and a matching `Origin` (no `cross_site_request_rejected`); unit cases for IPv6 literals, Origin with a path, Origin without `Host`; integration case for `X-Forwarded-Proto: https` from the trusted loopback peer.
- [x] 3.7 `dashboard-page.test.tsx`: keep the admin-seeded hydration guard and add the least-privilege boot-state hydration case; `apis-page.test.tsx` seeds write access.
- [x] 3.5 Run `ruff`, `ty`, focused pytest suites, `bun run lint`, `bun run typecheck`, `bun run test`, and strict OpenSpec validation.

## 4. Documentation

- [x] 4.2 `docs/deployment/remote.md`: reverse-proxy checklist bullet on preserving `Host` with port (nginx `$http_host`, Apache `ProxyPreserveHost On`), `admin-auth` in the Specs footer.
- [x] 4.1 Add "Cross-site request protection" to `docs/authentication.md` linking to the admin-auth spec.
