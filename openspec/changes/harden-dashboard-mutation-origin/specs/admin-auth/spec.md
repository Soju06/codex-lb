## ADDED Requirements

### Requirement: Dashboard mutations reject cross-site browser requests

The system SHALL evaluate every HTTP request whose method is not `GET`, `HEAD`, or `OPTIONS` and whose path starts with `/api/` before routing and before any authentication dependency, unless the path starts with `/api/fleet/` or `/api/codex/` or the request carries an `Authorization` header whose scheme is `Bearer` (case-insensitive) with a non-empty credential. For an evaluated request the system MUST apply, in order:

1. If a `Sec-Fetch-Site` header is present, the request is allowed only when its trimmed, lowercased value is `same-origin` or `none`.
2. Otherwise, if an `Origin` header is present, the request is allowed only when the header is not the literal `null` and its scheme, host, and port equal the request's own scheme (from the ASGI scope) and `Host` header, comparing hosts case-insensitively and treating an omitted port as 80 for `http` and 443 for `https`.
3. Otherwise (neither header present) the request is allowed.

A request that is not allowed MUST receive HTTP 403 with `content-type: application/json` and the dashboard error envelope `{"error": {"code": "cross_site_request_rejected", "message": "Cross-site dashboard requests are not allowed"}}`, and the downstream application MUST NOT be invoked. The system MUST log the rejection at warning level with the method, path, and either the `Sec-Fetch-Site` value or both the presented `Origin` (scheme, host, port; path and query stripped) and the expected origin derived from the request scheme and `Host` header, and MUST NOT log other header values. The check MUST NOT depend on whether a session cookie is present and MUST require no configuration. Requests that are not evaluated, including WebSocket scopes, MUST pass through unchanged.

#### Scenario: Cross-site fetch metadata is rejected before authentication

- **GIVEN** no dashboard session exists
- **WHEN** a client sends `POST /api/dashboard-auth/logout` or `PUT /api/settings` with `Sec-Fetch-Site: cross-site`
- **THEN** the system returns HTTP 403 with error code `cross_site_request_rejected`
- **AND** the route handler and its authentication dependencies do not run

#### Scenario: Same-origin fetch metadata passes

- **WHEN** a client sends `PUT /api/settings` with `Sec-Fetch-Site: same-origin`
- **THEN** the request reaches the route handler and is processed by the normal authentication and permission gates

#### Scenario: Origin is compared against the request's own origin

- **GIVEN** the request scheme is `https` and the `Host` header is `Dashboard.example`
- **WHEN** a client sends `POST /api/accounts/import` with `Origin: https://dashboard.example:443` and no `Sec-Fetch-Site` header
- **THEN** the request is allowed
- **WHEN** the same request carries `Origin: http://dashboard.example`, `Origin: https://dashboard.example:8443`, `Origin: https://evil.example`, or `Origin: null`
- **THEN** the system returns HTTP 403 with error code `cross_site_request_rejected`

#### Scenario: Requests without browser headers pass through

- **WHEN** a client sends `PUT /api/settings` with neither `Sec-Fetch-Site` nor `Origin`
- **THEN** the request reaches the route handler unchanged

#### Scenario: Safe methods, non-dashboard paths, and bearer routes are exempt

- **WHEN** a client sends `GET /api/dashboard-auth/session` with `Sec-Fetch-Site: cross-site`
- **THEN** the request is not evaluated and proceeds normally
- **WHEN** a client sends `POST /api/fleet/refresh` or `POST /api/codex/rate-limit-reset-credits/consume` with `Sec-Fetch-Site: cross-site`
- **THEN** the request is not evaluated and is left to bearer authentication
- **WHEN** a client sends `POST /api/settings/upstream-proxy/endpoints` with `Sec-Fetch-Site: cross-site` and `Authorization: Bearer <token>`
- **THEN** the request is not evaluated and proceeds to the route's own authentication

#### Scenario: Every dashboard mutation is covered

- **WHEN** a cross-site request (`Sec-Fetch-Site: cross-site`) is sent to every route under `/api/` whose method is not `GET`, `HEAD`, or `OPTIONS` and whose path does not start with `/api/fleet/` or `/api/codex/`
- **THEN** every response is HTTP 403 with error code `cross_site_request_rejected`, including the `/api/dashboard-auth/*` routes and `POST /api/dashboard-auth/logout`
- **WHEN** the same routes receive the same request with `Sec-Fetch-Site: same-origin`, or with no `Sec-Fetch-Site` header and an `Origin` equal to the request's own origin
- **THEN** no response carries error code `cross_site_request_rejected`; the request reaches the route and is answered by its own validation, authentication, and permission gates
