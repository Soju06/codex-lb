## Why
Issue #597 reports a 500 with a server-side log of `password cannot be longer than 72 bytes` while setting a dashboard password. The frontend then surfaces a generic "Unexpected error" with no actionable detail.

Root cause: `bcrypt.hashpw` enforces a hard 72-byte input limit on the encoded password (`bcrypt 5.x` raises `ValueError("password cannot be longer than 72 bytes ...")`). `_hash_password` in `app/modules/dashboard_auth/service.py` calls `bcrypt.hashpw` directly, and the two API entry points that lead there (`POST /api/dashboard-auth/password/setup`, `POST /api/dashboard-auth/password/change`) only validate `len(password) < 8` before hashing. There is no upper-bound validation, so the `ValueError` propagates out as an unhandled exception and surfaces as a 500.

## What Changes
- Add an explicit upper-bound check for password length at the API layer, alongside the existing 8-character minimum, before the password reaches `bcrypt.hashpw`.
- The check measures **UTF-8 encoded byte length**, not codepoint count, so multi-byte characters (emoji, non-ASCII letters) count for as many bytes as bcrypt itself counts. The limit constant `_MAX_PASSWORD_BYTES = 72` matches bcrypt's own ceiling exactly.
- Surface the failure as a structured `DashboardValidationError` (`HTTP 422`) with code `password_too_long`, so the dashboard frontend can show a clear "password must be at most 72 bytes" message instead of "Unexpected error".
- Apply identical validation to both endpoints (`/password/setup` and `/password/change`) by introducing a shared `_validate_password_length` helper that also handles the existing 8-character minimum, eliminating divergence between the two paths.
- Add regression tests covering:
  - rejection of 73-byte ASCII passwords with `password_too_long`
  - acceptance of exactly 72-byte passwords
  - rejection of strings whose codepoint count is small but whose UTF-8 byte length exceeds 72 (e.g. an emoji-only password)
  - identical behavior on `/password/change`'s `new_password`

## Impact
- Restores a clear, actionable 422 response for the documented failure case instead of a 500 with a confusing "Unexpected error" on the client.
- No change to bcrypt's own behavior or to how passwords are stored.
- No migration required.
- Existing valid passwords are unaffected; the new check matches bcrypt's existing ceiling, so any password that could already be set is still accepted.
