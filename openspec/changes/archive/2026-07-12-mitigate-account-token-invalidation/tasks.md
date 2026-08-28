## 1. Permanent Session Failures

- [x] 1.1 Classify `app_session_terminated` as a permanent re-authentication-required failure.
- [x] 1.2 Cover refresh and usage-refresh paths for that code.

## 2. Usage Refresh Cadence

- [x] 2.1 Stagger scheduler refresh work to one eligible account per slice.
- [x] 2.2 Preserve cycle-boundary cache invalidation behavior.
- [x] 2.3 Cover ordering, slice duration, and rotation behavior.

## 3. Routing Availability

- [x] 3.1 Add in-memory routing-unavailable tracking.
- [x] 3.2 Mark accounts unavailable on pause/delete/permanent refresh failure and clear on re-auth/reactivation/import.
- [x] 3.3 Prevent stale HTTP bridge sessions from being reused after an account becomes unavailable.

## 4. Codex Installation Id

- [x] 4.1 Add and backfill `accounts.codex_installation_id`.
- [x] 4.2 Strip inbound Codex installation ids and inject the server-owned account id into upstream response-create metadata.
- [x] 4.3 Cover HTTP/WebSocket metadata replacement.

## 5. Verification

- [x] 5.1 Run focused ruff, type checking, pytest, and OpenSpec validation.
