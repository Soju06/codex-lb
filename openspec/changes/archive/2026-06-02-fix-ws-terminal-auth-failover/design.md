# Design: WebSocket terminal auth failover

## Problem model

The WebSocket proxy has two different auth-failure surfaces:

1. Connect-time auth failure: upstream rejects the WebSocket handshake with HTTP 401. This already performs forced refresh and account failover.
2. Terminal in-band auth failure: upstream accepts the WebSocket, then returns `response.failed` or `error` with `invalid_api_key`/`authentication_error` before `response.created` or any visible output. This currently finalizes the request as a normal terminal error and does not refresh, fail over, or deactivate the selected account.

The second surface is account-local when it occurs before downstream-visible output and the request has a replayable `response.create` body. It is safe to suppress the failed terminal event and replay on a refreshed or alternate account because no response content has been shown to the client.

## Recovery policy

- Detect auth terminal events by normalized `invalid_api_key` or `authentication_error`.
- Treat messages containing `session has ended`, `log in again`, `session expired`, or `reauth` as explicit re-authentication-required signals.
- If the auth error is re-authentication-required, mark the account permanent with an internal `account_session_expired` code and replay excluding that account.
- If the auth error is generic and the request has not yet attempted an auth replay, reconnect to the same account with forced token refresh and replay once.
- If the same replay fails with terminal auth again, mark the account permanent with `account_auth_invalidated`, exclude it, and replay on another eligible account.

## Safety constraints

- Only replay when `_websocket_precreated_retry_error_code`-style pre-visible guards hold: one pending request, no `response_id`, no response events, `awaiting_response_created`, request text present, and replay count budget available.
- Do not replay after text deltas or other downstream-visible output.
- Do not silently switch account for owner-pinned `previous_response_id` continuations; those remain fail-closed unless the request has a safe fresh replay body.
- Preserve downstream compatibility by continuing to expose OpenAI-shaped auth errors when no safe recovery is possible.
