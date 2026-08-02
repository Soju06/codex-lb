# Make account-pool cutover resets terminal

## Summary

Preserve automatic account-neutral recovery for verified self-contained Responses full resends, while making unreplayable continuity loss after an API-key account-pool change terminate immediately with an actionable reset response.

## Why

When a Dashboard account assignment removes the owner of an existing Codex conversation, codex-lb already knows whether the incoming request contains a safe, self-contained full resend. Safe requests can be rebuilt on a currently assigned account. Unsafe incremental requests currently return HTTP 502 with `continuity_reset_required`; Codex treats that response as retryable and repeatedly reconnects even though the removed owner cannot become eligible again.

## What Changes

- Keep the existing one-shot account-neutral replay for verified self-contained full resends.
- Return HTTP 400 `invalid_request_error` with code `continuity_reset_required` when an assignment cutover permanently removes the required owner and safe replay cannot be proven.
- Identify `previous_response_id` as the reset-causing parameter and instruct the operator to start a new Codex conversation with `/new`.
- Preserve retryable 5xx behavior for temporary owner unavailability outside an account-assignment cutover.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: Distinguishes permanent assignment-cutover resets from temporary retryable continuity failures.
- `sticky-session-operations`: Keeps hard ownership fail-closed while allowing verified full-resend reconstruction on a new assigned account.

## Non-Goals

- No transparent replay of incremental tool output, file-backed input, encrypted turn state, or opaque `conversation` state.
- No automatic replay of the current request after a terminal reset response.
- No changes to production process state or port 2455.
