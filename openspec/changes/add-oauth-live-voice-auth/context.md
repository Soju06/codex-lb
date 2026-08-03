# OAuth WebRTC Live Voice Context

Normative behavior lives in this change's capability delta specs. This file records the local evidence and operational boundary.

## Sanitized evidence

- ChatGPT.app 26.727.51351 bundles Codex `0.146.0-alpha.9.2`.
- A local app-server WebRTC probe observed `POST /backend-api/codex/realtime/calls` with Bearer authorization and `chatgpt-account-id` present.
- The same probe observed sideband `GET /v1/realtime` with `intent` and `call_id` query keys plus Bearer authorization and `chatgpt-account-id` present.
- The probe retained no credential value, account id, call id, SDP payload, audio, transcript, or frame body.

## Operator example

1. An operator imports several upstream ChatGPT accounts through existing Codex-LB flows.
2. On the caller seat's Account detail, the operator enables OAuth Live Voice and selects the accounts allowed to serve its calls.
3. Official Codex keeps `model_provider = "openai"` and routes WebRTC call creation to `http://127.0.0.1:2455/backend-api/codex` and sideband to `http://127.0.0.1:2455/v1`.
4. Codex-LB verifies the OAuth caller, selects within the configured set, binds the final call owner, and attaches sideband to that owner.
5. Registered Proxy API Key clients continue using their existing assignments, limits, logs, and affinity digests.

## Operational boundary

The policy controls access to pooled upstream accounts; it does not alter or copy OAuth credentials. Existing cross-machine authorization-row handling remains outside this feature. `refresh_token_reused` remains an operator-managed recovery event. Logs and acceptance evidence use only routes, status, counts, hashes, and presence booleans.

