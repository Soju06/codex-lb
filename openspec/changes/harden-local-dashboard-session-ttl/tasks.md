## 1. Backend auth behavior

- [x] 1.1 Add a shared resolver for effective dashboard session TTL.
- [x] 1.2 Apply the resolver when issuing password, guest, and TOTP-verified dashboard sessions.
- [x] 1.3 Clamp long configured lifetimes to 12 hours for non-loopback or proxy/trusted-header requests.
- [x] 1.4 Preserve shorter configured lifetimes.
- [x] 1.5 Add an explicit loopback-host-header override for localhost-published deployments where the socket peer is a bridge address.

## 2. Settings default and migration

- [x] 2.1 Change new dashboard settings defaults to 1 year.
- [x] 2.2 Add an Alembic migration that updates old-default 12-hour settings rows to 1 year and preserves customized rows.
- [x] 2.3 Update frontend fallback defaults to match the backend default.

## 3. Verification

- [x] 3.1 Add unit tests for direct loopback, bridge-without-override, bridge-with-override, remote, proxy, and shorter-TTL resolver behavior.
- [x] 3.2 Add/update API tests proving cookie `Max-Age` uses the effective TTL.
- [x] 3.3 Run focused backend and frontend validation.
- [ ] 3.4 Run OpenSpec validation. Blocked locally: neither `openspec` nor `uv run openspec` is available in this environment.
