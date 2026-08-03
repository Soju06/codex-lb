# Codex Live Voice

codex-lb keeps Codex Live Voice call creation and its control sideband on the same ChatGPT account. This matters in an account pool: the account that successfully creates a call is the only account that can safely join its sideband.

!!! note "Private Codex compatibility"
    This capability supports the private routes used by the installed Codex app. It does not implement OpenAI's public Realtime API, `POST /v1/realtime/calls`, or `POST /v1/realtime/client_secrets`, and it does not proxy WebRTC media.

## Caller authentication

Live Voice accepts two caller types on the same private routes:

- A registered [proxy API key](api-keys.md) keeps its current assignments, limits, attribution, and call ownership behavior.
- A first-party Codex ChatGPT OAuth bearer requires an imported caller Account plus an active OAuth Live Voice policy with an explicit allowed-account pool. Configure this policy in the selected Account's dashboard detail card.

Tokens beginning with `sk-clb-` always follow proxy-key validation. Other bearer tokens require `chatgpt-account-id`, upstream credential verification, exact imported-seat resolution, and current policy authorization before account selection.

The feature adds no `CODEX_LB_*` setting or navigation item. Upgrade creates empty policy tables, so OAuth Live Voice starts disabled while existing proxy-key Live behavior remains available.

## First-party Codex routing

Keep the built-in OpenAI provider and route the two Live Voice legs to codex-lb:

```toml
model_provider = "openai"
experimental_realtime_webrtc_call_base_url = "http://127.0.0.1:2455/backend-api/codex"
experimental_realtime_ws_base_url = "http://127.0.0.1:2455/v1"
```

The current Codex client appends `/realtime/calls` to the WebRTC base and `/realtime?intent=...&call_id=...` to the WebSocket base. Re-run a real route probe after upgrading the bundled Codex client because these experimental URL contracts can change.

## Supported private routes

A compatible Codex client uses these routes as one account-bound workflow:

- `POST /backend-api/codex/realtime/calls` creates the call.
- `WS /backend-api/codex/{call_id}` joins through the current installed-app form for bounded `rtc_...` or canonical UUID call ids; unrelated Codex WebSocket paths keep their ordinary behavior.
- `WS /v1/live/{call_id}` joins through the v3 form.
- `WS /v1/realtime?call_id={call_id}` joins through the legacy form.

codex-lb validates the call id returned in the successful call-creation `Location`, ignoring private query or fragment context after the first `?`, binds it to the final successful account under the authenticated caller scope, and routes every supported sideband form back to that exact account. Attachment fails closed when the caller, assignment or policy, account state, or ownership binding is no longer valid. A bound call always returns to its original owner.

## Privacy and request history

The ownership record contains only a caller-scoped digest and the owning account reference. Raw call ids, proxy keys, OAuth tokens, SDP, attestation values, and realtime frame bodies are excluded. Call-creation SDP is excluded from payload traces, and sideband frames are excluded from Responses archives.

The dashboard's Recent Requests data accepts the sideband as a typed `realtime_live` WebSocket request. Private call-creation and sideband rows omit account identity, model content, upstream error text, failure metadata, live query text, and credentials. The internal ownership record stays hidden from ordinary sticky-session lists and delete operations.

## Failure behavior

- `401 invalid_api_key` means the supplied proxy key or OAuth bearer/account pair did not authenticate.
- `403 oauth_live_not_enabled` means the verified imported caller has no active, non-empty OAuth Live policy.
- `400 invalid_realtime_call_id` means the sideband supplied a malformed or ambiguous call id.
- `503 realtime_call_binding_failed` means a successful upstream call could not be bound safely; codex-lb does not replay that call through another account.

---

*Spec: [realtime-api-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/realtime-api-compat)*
