# Codex Live Voice

codex-lb keeps Codex Live Voice call creation and its control sideband on the same ChatGPT account. This matters in an account pool: the account that successfully creates a call is the only account that can safely join its sideband.

!!! note "Private Codex compatibility"
    This capability supports the private routes used by the installed Codex app. It does not implement OpenAI's public Realtime API, `POST /v1/realtime/calls`, or `POST /v1/realtime/client_secrets`, and it does not proxy WebRTC media.

## Requirement: a registered proxy key

Live Voice routes always require an existing registered [proxy API key](api-keys.md), even when ordinary proxy API-key authentication is disabled. Missing or unregistered keys are rejected before codex-lb selects or contacts an upstream account.

No new `CODEX_LB_*` setting, migration, dependency, or setup step is required. Operators who do not use Live Voice can continue running the base proxy and dashboard unchanged.

## Codex Desktop with a custom provider

When Desktop uses a custom provider whose Responses base URL ends in `/v1`,
configure both voice endpoints in the client's `~/.codex/config.toml`:

```toml
# Top-level settings: place before any [table] header.
experimental_realtime_webrtc_call_base_url = "https://your-codex-lb.example/backend-api/codex"
experimental_realtime_ws_base_url = "https://your-codex-lb.example/v1"
```

Replace the example origin with your codex-lb origin. Keep the existing
Responses provider configuration and registered proxy key. These are client
settings, not server environment variables; configure each machine that runs
the Codex voice session. Updating the server does not update client files.

The two settings cover different connections:

- The call-creation URL makes the client send the backend JSON request to
  `/backend-api/codex/realtime/calls`. Without it, a `/v1` provider can select
  the unsupported multipart `POST /v1/live` route and receive HTTP 405.
- The WebSocket URL directs the WebRTC control sideband through codex-lb,
  where the registered key resolves the account that created the call.
  Configuring call creation alone can leave the sideband using its direct
  OpenAI default and failing authentication. `supports_websockets = true`
  in a Responses provider does not configure this separate voice connection.

These experimental settings were verified with the Codex Desktop bundled
CLI `0.159.0-alpha.12.1`. Client behavior may change between releases; check
support in the installed client before applying them to older versions.
If an existing session retains its old configuration, reload the client
when active work can safely be interrupted.

To verify the setup, start a voice session and check that call creation
succeeds and the `/v1/live/{call_id}` WebSocket is accepted. Then speak a
short phrase, confirm the transcript and a backend handoff, and listen for
the reply. A successful call-creation response alone does not verify voice.
WebRTC audio still travels directly between the client and OpenAI; codex-lb
proxies call creation and the control sideband, not the media stream.

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
