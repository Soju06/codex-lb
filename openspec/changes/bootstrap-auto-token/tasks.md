## 1. Backend Core

- [x] 1.1 Create `app/core/bootstrap.py` with shared encrypted token helpers and replica-safe accessors
- [x] 1.2 Wire shared token generation into `app/main.py` lifespan after `init_db()` — log token with visual delimiters via `logger.info()`
- [x] 1.3 Update `app/modules/dashboard_auth/api.py` session endpoint to use `get_active_bootstrap_token()` instead of direct env var read
- [x] 1.4 Update `app/modules/dashboard_auth/api.py` `setup_password()` and repository password write path to consume the shared token atomically after success
- [x] 1.5 Add `dashboard_settings.bootstrap_token_encrypted` migration for shared storage
- [x] 1.6 Harden bootstrap token handling for non-ASCII manual tokens and immediate regeneration after password removal
- [x] 1.7 Persist only a hash of the auto-generated bootstrap token and avoid re-logging stored tokens on restart
- [x] 1.8 Read shared bootstrap state uncached across replicas, rotate-and-log a fresh token on restart, and make frontend copy source-agnostic

## 2. Tests

- [x] 2.1 Create `tests/unit/test_bootstrap.py` — unit tests for priority chain and shared token resolution
- [x] 2.2 Create `tests/integration/test_dashboard_bootstrap.py` — integration tests for full remote bootstrap flow with auto-generated/manual tokens and cross-instance validation

## 3. Frontend

- [x] 3.1 Update `frontend/src/features/auth/components/bootstrap-setup-screen.tsx` — change messaging to reference server logs
- [x] 3.2 Update `frontend/src/features/settings/components/password-settings.tsx` — change remote setup messaging to reference server logs

## 4. Documentation

- [x] 4.1 Add concise "Remote Setup" section to `README.md` after Quick Start — cover auto-generated token, docker logs, manual env var option, and shared token behavior
- [x] 4.2 Create `openspec/specs/admin-auth/context.md` with full bootstrap behavior documentation (SoT)
