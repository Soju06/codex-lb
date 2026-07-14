# Add Fixed-Endpoint Native Client Routing Controls

## Why

Codex App/CLI and a compatible OpenClaw Codex-native provider can stay attached
to `http://127.0.0.1:2455/backend-api/codex` while choosing whether the next
native OAuth turn uses the account signed in to the client or an eligible
`codex-lb` pool account. Today that choice normally requires changing provider
configuration and reopening a client or app-server.

This proposal narrows and hardens the design discussed in
[Discussion #1306](https://github.com/Soju06/codex-lb/discussions/1306). The
routine switch becomes a server-side credential-source policy, without a new
client secret, custom model selection, or generic `/v1` compatibility path.

## What Changes

- Add an opt-in, persisted `pool | client_oauth` policy for qualifying native
  OAuth traffic. Existing and new installations default to `pool`.
- Reuse the documented no-key trusted-loopback model. OAuth switching is
  available only while `api_key_auth_enabled=false`; enabling API-key auth
  makes base-valid native OAuth routing fail closed after its authoritative read
  with a stable incompatibility error.
- Keep every `sk-clb-*` request outside this policy. It continues through the
  existing API-key validation, restrictions, pool selection, logging, and
  accounting behavior under either policy value.
- Add a bounded native OAuth classifier and direct relay with exact
  method/path, fingerprint, header, origin, and redirect rules. OAuth values
  remain request- or downstream-WebSocket-local and never enter persistent or
  browser-visible state. Security admission runs first, existing route/base
  validation runs second, and only a base-valid candidate reads authoritative
  policy before routing.
- After base validation, fail closed instead of silently using the pool when
  `client_oauth` is active and an otherwise recognized native request omits its
  OAuth identity pair.
- Keep generic top-level `/v1` traffic, model-source routing, and unsupported
  clients unchanged.
- Change WebSocket source at an internal turn boundary. The server retains a
  newly accepted `response.create`, applies existing base turn validation, then
  reads authoritative policy and opens the selected upstream. A base-invalid
  create fails without a policy read. Each valid turn binds to exactly one
  policy source kind: `pool` or `client_oauth`. `client_oauth` keeps the exact
  request OAuth identity and never falls back to the pool; `pool` retains
  existing retry-safe pre-visible account failover where the base Responses
  contracts allow it. Neither policy kind may change mid-turn, and no replay or
  failover may occur after `response.created` or visible output. The downstream
  client stays connected and an in-flight turn finishes under its original
  policy kind.
- Treat upstream response anchors, supported `input_file.file_id` references,
  and file finalize operations as account-scoped. A policy-source-kind change
  rebases a stale `previous_response_id` only when the existing Responses
  contract already has a retry-safe full-context body; otherwise it fails
  before send. Supported input-file work is source/owner-bound and must be
  re-uploaded after a mismatch or loss of bounded provenance. Unsupported
  `input_image.file_id` and `sediment://` image references retain the base
  `400 unsupported_input_image_format` rejection before authoritative policy
  read, provenance, or routing.
- Add a capability-gated, standalone `/native-routing` page whose primary
  one-click action changes only this policy. Static one-time attachment
  guidance may appear below it; live attachment status and recovery operations
  are deferred to a separate future companion proposal.
- Support OpenClaw only through a compatible Codex-native provider that emits
  an allowlisted Codex protocol fingerprint. An OpenClaw User-Agent suffix may
  label the client but never authenticates it.

## Impact

- Affected specs: new `native-client-routing`; conditional precedence for
  `responses-api-compat`, `files-upload-protocol`, `images-api-compat`,
  `audio-transcriptions-compat`, and `model-catalog-compat`; added
  `frontend-architecture` requirements
- Affected backend: settings migration, authenticated policy API, authoritative
  per-request/per-turn reads, native HTTP/WebSocket relay, security and
  compatibility tests
- Affected frontend: isolated page, policy control, compatibility state,
  routing explanation, and static attachment guidance
- Server opt-in: `CODEX_LB_NATIVE_ROUTING_ENABLED=true`
- Unchanged outside qualifying native policy traffic: client OAuth stores,
  client-selected model/reasoning/requested service tier, existing API-key
  behavior, generic `/v1`, and normal pool logic

The proposed `DOMANHDUC · dmdfami` page attribution is intentionally presented
for maintainer approval. It is not a hidden merge or acceptance requirement.
Policy/configuration state proves neither quota ownership for a completed turn
nor the actual Fast tier; those claims require bounded live evidence.
