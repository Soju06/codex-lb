# Codex Live Voice

codex-lb keeps Codex Live Voice call creation and its control sideband on the same ChatGPT account. This matters in an account pool: the account that successfully creates a call is the only account that can safely join its sideband.

!!! note "Private Codex compatibility"
    This capability supports the private routes used by the installed Codex app. It does not implement OpenAI's public Realtime API, `POST /v1/realtime/calls`, or `POST /v1/realtime/client_secrets`, and it does not proxy WebRTC media.

## Requirement: a registered proxy key

Live Voice routes always require an existing registered [proxy API key](api-keys.md), even when ordinary proxy API-key authentication is disabled. Missing or unregistered keys are rejected before codex-lb selects or contacts an upstream account.

No new `CODEX_LB_*` setting, migration, dependency, or setup step is required. Operators who do not use Live Voice can continue running the base proxy and dashboard unchanged.

## Supported private routes

A compatible Codex client uses these routes as one account-bound workflow:

- `POST /backend-api/codex/realtime/calls` creates the call.
- `WS /backend-api/codex/{call_id}` joins through the current installed-app form for bounded `rtc_...` or canonical UUID call ids; unrelated Codex WebSocket paths keep their ordinary behavior.
- `WS /v1/live/{call_id}` joins through the v3 form.
- `WS /v1/realtime?call_id={call_id}` joins through the legacy form.

codex-lb validates the call id returned in the successful call-creation `Location`, ignoring private query or fragment context after the first `?`, binds it to the final successful account under the caller's proxy key, and routes every supported sideband form back to that exact account. Attachment fails closed if the key, assignment, account state, or ownership binding is no longer valid; codex-lb does not refresh credentials or substitute another account after a call is created.

## Privacy and request history

The ownership record contains only an API-key-scoped digest and the owning account reference. Raw call ids, proxy keys, OAuth tokens, SDP, attestation values, and realtime frame bodies are not stored in that record. Call-creation SDP is excluded from payload traces, and sideband frames are not added to Responses archives.

The dashboard's Recent Requests data accepts the sideband as a typed `realtime_live` WebSocket request. Private call-creation and sideband rows omit account identity, model content, upstream error text, failure metadata, live query text, and credentials. The internal ownership record stays hidden from ordinary sticky-session lists and delete operations.

## Failure behavior

- `401 invalid_api_key` means the request did not carry a registered proxy key.
- `400 invalid_realtime_call_id` means the sideband supplied a malformed or ambiguous call id.
- `503 realtime_call_binding_failed` means a successful upstream call could not be bound safely; codex-lb does not replay that call through another account.

---

*Spec: [realtime-api-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/realtime-api-compat)*
