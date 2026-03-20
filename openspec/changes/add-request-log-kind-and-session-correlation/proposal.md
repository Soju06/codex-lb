## Why

Operators can now see request `transport` in the dashboard, but compact incidents still require cross-checking raw server logs because the recent requests table does not tell them whether a row is a normal Responses call or a compact call, and it does not expose any safe correlation key for the Codex `session_id` thread.

This makes compact regressions harder to prove from the UI: a row can show `HTTP` while still being either `/responses` or `/responses/compact`, and it is hard to tell whether a compact request and the next follow-up response stayed on the same session thread.

## What Changes

- persist a stable `request_kind` on `request_logs` for proxied requests
- persist a safe hashed `session_id` correlation key on `request_logs` when the inbound request carries `session_id`
- expose both fields through `GET /api/request-logs`
- render the request kind and session correlation key in the dashboard recent requests table
- add dashboard request-log filters and facet options for `requestKind` and `transport`

## Impact

- adds request-log schema fields and a DB migration
- updates proxy request-log writes for Responses, compact, websocket Responses, and transcription flows
- extends request-log filter contracts in both backend and frontend
- improves compact incident debugging without exposing raw session identifiers
