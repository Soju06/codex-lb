## 1. Dependencies & TOTP Library Migration

- [x] 1.1 Add `bcrypt` and `pyotp` to `pyproject.toml` dependencies
- [x] 1.2 Replace `app/core/auth/totp.py` internals with `pyotp` — preserve public interface (`generate_totp_secret`, `verify_totp_code`, `build_otpauth_uri`, `TotpVerificationResult`)
- [x] 1.3 Remove custom HMAC-SHA1 TOTP implementation; verify existing TOTP tests pass with `pyotp` backend

## 2. Database Schema & Migrations

- [x] 2.1 Add `password_hash` (TEXT, nullable) and `api_key_auth_enabled` (BOOLEAN, default false) columns to `DashboardSettings` model
- [x] 2.2 Create `ApiKey` ORM model in `app/db/models.py` with columns: `id`, `name`, `key_hash`, `key_prefix`, `allowed_models`, `weekly_token_limit`, `weekly_tokens_used`, `weekly_reset_at`, `expires_at`, `is_active`, `created_at`, `last_used_at`; add `idx_api_keys_hash` index
- [x] 2.3 Add `api_key_id` (TEXT, nullable) column to `RequestLog` model
- [x] 2.4 Create migration `add_dashboard_settings_password` — adds `password_hash` and `api_key_auth_enabled` to `dashboard_settings`
- [x] 2.5 Create migration `add_api_keys` — creates `api_keys` table and adds `api_key_id` to `request_logs`

## 3. Settings Cache

- [x] 3.1 Implement `SettingsCache` in `app/core/config/settings_cache.py` — in-memory singleton with 5s TTL, `get()` (loads from DB if stale), `invalidate()` (clears cache)
- [x] 3.2 Wire `SettingsCache` into middleware and expose `invalidate()` for use by settings/auth endpoints

## 4. Admin Auth — Password Service Layer

- [x] 4.1 Add password schemas in `app/modules/dashboard_auth/schemas.py` — `PasswordSetupRequest`, `PasswordLoginRequest`, `PasswordChangeRequest`, `PasswordRemoveRequest`; update `DashboardAuthSessionResponse` to include `password_required` field
- [x] 4.2 Add password repository methods in `app/modules/dashboard_auth/repository.py` — `set_password_hash()`, `get_password_hash()`, `clear_password_and_totp()`
- [x] 4.3 Extend `DashboardSessionStore` — update `create()` signature to `create(*, password_verified: bool, totp_verified: bool)`, update `get()` to parse `pw` field, reject sessions without `pw` field
- [x] 4.4 Implement password methods in `DashboardAuthService` — `setup_password()` (bcrypt hash, reject if exists), `verify_password()` (bcrypt check), `change_password()` (verify old + hash new), `remove_password()` (verify + clear hash + clear TOTP)
- [x] 4.5 Update `get_session_state()` to compute `password_required` (password_hash is not None) and adjust `authenticated` logic for password + TOTP layers

## 5. Admin Auth — Password API Endpoints

- [x] 5.1 Add `POST /api/dashboard-auth/password/setup` endpoint — validate min 8 chars, reject if password exists (409), return session cookie
- [x] 5.2 Add `POST /api/dashboard-auth/password/login` endpoint — verify bcrypt, rate limit (8 failures/60s), return session cookie
- [x] 5.3 Add `POST /api/dashboard-auth/password/change` endpoint — require valid session, verify current password, hash new password
- [x] 5.4 Add `DELETE /api/dashboard-auth/password` endpoint — require valid session, verify password, clear password + TOTP, clear cookie
- [x] 5.5 Add login rate limiter instance (`_password_rate_limiter`) using existing `TotpRateLimiter` pattern

## 6. TOTP Setup Flow Migration

- [x] 6.1 Remove `X-Codex-LB-Setup-Token` header validation from TOTP setup/confirm endpoints in `dashboard_auth/api.py`
- [x] 6.2 Remove `CODEX_LB_DASHBOARD_SETUP_TOKEN` from `app/core/config/settings.py` and `.env.example`
- [x] 6.3 Update TOTP verify endpoint to issue session with `pw=true, tv=true` (require existing `pw=true` session)
- [x] 6.4 Update TOTP disable endpoint to require `pw=true` session instead of setup token

## 7. Unified Auth Middleware

- [x] 7.1 Replace `add_dashboard_totp_middleware` with `add_auth_middleware` in `app/core/middleware/dashboard_auth.py` — implement path-based routing: PUBLIC_PATHS → pass, PROXY_PREFIXES → API Key check, `/api/*` → session check, else → pass
- [x] 7.2 Implement session validation branch — check `password_hash` (None = pass), validate cookie `pw` field, check `totp_required_on_login` + `tv` field
- [x] 7.3 Implement API Key validation branch — check `api_key_auth_enabled` (false = pass), extract Bearer token, sha256 lookup, validate active/expired/weekly limit, store in `request.state.api_key`
- [x] 7.4 Implement lazy weekly reset in API Key validation — if `weekly_reset_at < now()`, atomically reset counter and advance date
- [x] 7.5 Update `app/main.py` to call `add_auth_middleware` instead of `add_dashboard_totp_middleware`
- [x] 7.6 Invalidate settings cache on all settings-modifying endpoints (password setup/change/remove, TOTP setup/disable, PUT /api/settings)

## 8. API Keys Module

- [x] 8.1 Create `app/modules/api_keys/schemas.py` — `ApiKeyCreateRequest`, `ApiKeyCreateResponse` (includes plain key), `ApiKeyResponse` (no key/hash), `ApiKeyUpdateRequest`, `ApiKeyListResponse`
- [x] 8.2 Create `app/modules/api_keys/repository.py` — `create()`, `get_by_id()`, `get_by_hash()`, `list_all()`, `update()`, `delete()`, `increment_weekly_usage()`, `reset_weekly_usage()`
- [x] 8.3 Create `app/modules/api_keys/service.py` — `create_key()` (generate sk-clb-..., sha256, store hash), `list_keys()`, `update_key()`, `delete_key()`, `regenerate_key()`, `validate_key()` (lookup + active/expired/limit checks + lazy weekly reset)
- [x] 8.4 Create `app/modules/api_keys/api.py` — router with prefix `/api/api-keys`: `POST /` (create), `GET /` (list), `PATCH /{id}` (update), `DELETE /{id}` (delete), `POST /{id}/regenerate`
- [x] 8.5 Add `ApiKeysContext` dataclass and `get_api_keys_context()` provider in `app/dependencies.py`
- [x] 8.6 Register `api_keys_api.router` in `app/main.py`

## 9. Proxy Integration — Model Restriction & Usage Tracking

- [x] 9.1 Add model restriction check in `proxy/api.py` — before calling service, read `request.state.api_key`, if `allowed_models` is set and model not in list, return 403
- [x] 9.2 Update `v1_models` endpoint — if `request.state.api_key` has `allowed_models`, filter `MODEL_CATALOG` to only allowed entries
- [x] 9.3 Update `ProxyService._stream_once()` finally block — if `request.state.api_key` exists and tokens are available, call `api_keys_repository.increment_weekly_usage(key_id, input_tokens + output_tokens)`
- [x] 9.4 Update `RequestLog` add_log calls to pass `api_key_id` from `request.state.api_key`
- [x] 9.5 Update `RequestLogsRepository.add_log()` to accept and persist `api_key_id`

## 10. Settings Module Update

- [x] 10.1 Add `api_key_auth_enabled` to settings schemas (`SettingsResponse`, `SettingsUpdateRequest`)
- [x] 10.2 Update `SettingsRepository` and `SettingsService` to handle `api_key_auth_enabled` field
- [x] 10.3 Invalidate settings cache in `PUT /api/settings` endpoint after successful update

## 11. Frontend — Login UI

- [x] 11.1 Add login form HTML to `index.html` — password input, submit button, error display; conditionally visible when `passwordRequired && !authenticated`
- [x] 11.2 Add TOTP verification HTML dialog to `index.html` — 6-digit input with auto-focus, replacing all `window.prompt()` usage
- [x] 11.3 Implement login flow in `index.js` — on SPA load call `/api/dashboard-auth/session`, if `passwordRequired` and not `authenticated` show login form, hide all tabs
- [x] 11.4 Implement password login submit handler — call `POST /api/dashboard-auth/password/login`, on success check if TOTP required, show TOTP dialog or load dashboard
- [x] 11.5 Implement TOTP dialog submit handler — call `POST /api/dashboard-auth/totp/verify`, on success reload session state and show full UI
- [x] 11.6 Add logout button to header — call `POST /api/dashboard-auth/logout`, clear state and show login form

## 12. Frontend — Settings Page Auth Management

- [x] 12.1 Add Password management section to settings — setup form (when no password), change password form (when password set), remove password button with confirmation
- [x] 12.2 Update TOTP settings section — remove setup token input, show TOTP setup/disable only when password is configured, use HTML dialog for QR code and code input
- [x] 12.3 Add API Key auth toggle (`apiKeyAuthEnabled`) to settings with warning text

## 13. Frontend — API Key Management UI

- [x] 13.1 Add API Keys section to settings page — table with columns: name, prefix, models, weekly usage/limit, expiry, status, actions
- [x] 13.2 Add API Key create dialog — name input, model multi-select (from MODEL_CATALOG), weekly token limit input, expiry date picker
- [x] 13.3 Add API Key creation result dialog — show full plain key with copy button and "will not be shown again" warning
- [x] 13.4 Add API Key edit dialog — update name, models, limit, expiry, active toggle
- [x] 13.5 Add API Key delete confirmation and regenerate confirmation dialogs
- [x] 13.6 Wire all API Key UI actions to `POST/GET/PATCH/DELETE /api/api-keys` endpoints

## 14. Cleanup & Config Removal

- [x] 14.1 Remove `dashboard_setup_token` field from `app/core/config/settings.py`
- [x] 14.2 Remove `CODEX_LB_DASHBOARD_SETUP_TOKEN` from `.env.example` and any documentation references
- [x] 14.3 Remove the `generate_totp_code()` function from `app/core/auth/totp.py` if unused after pyotp migration

## 15. Tests

- [x] 15.1 Unit tests for password service — setup, login, change, remove (bcrypt verification, edge cases)
- [x] 15.2 Unit tests for session store — cookie creation with `pw`/`tv` fields, backward-incompatible cookie rejection, expiration
- [x] 15.3 Unit tests for TOTP with pyotp — verify migration preserves behavior (generate, verify, replay protection, URI generation)
- [x] 15.4 Unit tests for API Key service — create (hash storage, prefix), validate (active/expired/limit/weekly reset), regenerate
- [x] 15.5 Unit tests for settings cache — TTL expiration, manual invalidation
- [x] 15.6 Integration tests for auth middleware — session branch (all scenarios: no password, password only, password+TOTP), API key branch (all scenarios: disabled, valid, invalid, expired, over limit)
- [x] 15.7 Integration tests for password endpoints — setup/login/change/remove flows, rate limiting
- [x] 15.8 Integration tests for API Key endpoints — CRUD, regenerate, model restriction on proxy, usage tracking
