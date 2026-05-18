## Why
Issue #383 reports that accounts whose upstream session has expired keep returning `token_expired` (HTTP 401 with `{"error": {"code": "token_expired"}}`) on every request, yet codex-lb keeps the account in `ACTIVE` state. The load balancer continues to route real client traffic to it, every request fails the same way, and the only mitigation is for an operator to manually pause the account.

The reporter's diagnosis is correct against the current code:

- `PERMANENT_FAILURE_CODES` in `app/core/balancer/logic.py` only lists `refresh_token_expired`, `refresh_token_reused`, `refresh_token_invalidated`, `account_deactivated`, `account_suspended`, and `account_deleted`. It does **not** include `token_expired`.
- `classify_refresh_error("token_expired")` therefore returns `False`, so `RefreshError` for this case is built with `is_permanent=False`.
- `AuthManager.refresh_account` only deactivates the account when the error is permanent, and `usage._should_deactivate_for_usage_error` only deactivates on HTTP 402/404 or the existing permanent set, so a refresh-time `token_expired` slips through both paths.
- Result: refresh keeps failing, the account stays `ACTIVE`, and the load balancer keeps selecting it. The reporter ran into exactly this with `email_3e2ba4c18bda` until they manually paused it.

The cause is specifically that `token_expired` is reaching codex-lb on the **token refresh** endpoint. Access-token-only expiry would have caused the refresh call itself to return a fresh token pair, not to fail with `token_expired`. So a `token_expired` at the refresh boundary unambiguously means the refresh token (or the session it represents) is no longer usable, which is the same shape as `refresh_token_expired` and the other entries already classified as permanent.

## What Changes
- Add `token_expired` to `PERMANENT_FAILURE_CODES` in `app/core/balancer/logic.py` with the message `"Authentication token expired - re-login required"`. This single change flows through both the `AuthManager.refresh_account` deactivation path (via `classify_refresh_error`) and the usage-refresh deactivation path (via `_should_deactivate_for_usage_error`'s `code in PERMANENT_FAILURE_CODES` check) without further plumbing.
- Add a unit regression in `tests/unit/test_auth_refresh.py` pinning `classify_refresh_error("token_expired") is True`.
- Add a unit regression in `tests/unit/test_auth_manager.py` that drives `refresh_account` against a stubbed `refresh_access_token` raising `RefreshError("token_expired", ..., classify_refresh_error("token_expired"))` and asserts the account is marked `DEACTIVATED` with a re-login-required reason.

## Impact
- Accounts that the upstream session has truly invalidated stop receiving traffic and are surfaced as `DEACTIVATED` with a clear reason in the dashboard, instead of looping `token_expired` failures while staying `ACTIVE`.
- No false positives expected: a still-valid refresh token returns a fresh token pair (HTTP 200), so `token_expired` only appears when the refresh itself has failed in a non-recoverable way.
- Operators no longer need to pause a clearly broken account manually; the next failing refresh will surface the deactivation. Re-authentication clears the deactivation as it does today for the existing permanent codes.
- No schema or migration changes. Behavior for the rest of the permanent codes is unchanged.
