## 1. Backend Core

- [ ] 1.1 Create `app/core/bootstrap.py` with `_auto_generated_token`, `get_active_bootstrap_token()`, `maybe_generate_bootstrap_token(password_exists: bool)`, `clear_auto_generated_token()`
- [ ] 1.2 Wire `maybe_generate_bootstrap_token()` into `app/main.py` lifespan after `init_db()` — log token with visual delimiters via `logger.info()`
- [ ] 1.3 Update `app/modules/dashboard_auth/api.py` session endpoint to use `get_active_bootstrap_token()` instead of direct env var read
- [ ] 1.4 Update `app/modules/dashboard_auth/api.py` `setup_password()` to use `get_active_bootstrap_token()` for validation and call `clear_auto_generated_token()` after success

## 2. Tests

- [ ] 2.1 Create `tests/unit/test_bootstrap.py` — unit tests for priority chain, generation conditions, clear behavior
- [ ] 2.2 Create `tests/integration/test_dashboard_bootstrap.py` — integration tests for full remote bootstrap flow with auto-generated and manual tokens

## 3. Frontend

- [ ] 3.1 Update `frontend/src/features/auth/components/bootstrap-setup-screen.tsx` — change messaging to reference server logs
- [ ] 3.2 Update `frontend/src/features/settings/components/password-settings.tsx` — change remote setup messaging to reference server logs

## 4. Documentation

- [ ] 4.1 Add concise "Remote Setup" section to `README.md` after Quick Start — cover auto-generated token, docker logs, and manual env var option
- [ ] 4.2 Create `openspec/specs/admin-auth/context.md` with full bootstrap behavior documentation (SoT)
