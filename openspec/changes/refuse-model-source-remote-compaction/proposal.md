# Refuse remote compaction for model-source models before account selection

## Why

A Codex CLI session configured with a model that an OpenAI-compatible model
source serves (for example `openrouter/auto`) routes every ordinary turn to that
source. Its remote compaction request is the same `POST
/backend-api/codex/responses` call with a terminal `compaction_trigger` item,
which the proxy currently excludes from source routing and hands to the
subscription compact flow instead. A model source cannot emit a `compaction`
output item, so that request can never succeed there; it only consumes a
subscription account selection, an admission lease, and a usage reservation,
and then answers with whatever the account pool says. With every ChatGPT
account at its weekly limit the client saw a `usage_limit_reached` 429 for a
model that never touches a ChatGPT account, and kept retrying.

## What Changes

- Add one shared refusal that runs before any subscription account selection,
  admission gate, or reservation: when a terminal `compaction_trigger` on
  `POST /backend-api/codex/responses`, or a `POST /backend-api/codex/responses/compact`
  or `POST /v1/responses/compact` request, names a model that an enabled
  Responses-capable model source serves, answer HTTP 400
  `invalid_request_error` with code `compaction_unsupported`.
- Decide source ownership with the ordinary source-selection rules (raw client
  alias then normalized model, API key model allowlist, source assignment
  scope, subscription-registry precedence), so subscription models are never
  affected and disabled sources keep the existing `model_source_disabled`
  behaviour.
- Log the refusal through the existing proxy error-response path; no new
  logging path and no request log row for a dispatch that never happened.

## Non Goals

- Serving compaction through a model source, or synthesizing a local
  compaction summary in the proxy. The client compacts locally.
- Changing `/v1/responses`, where a terminal `compaction_trigger` remains
  eligible for source routing as an ordinary Responses input item.
- Changing the WebSocket transport's source-ownership guards.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: a Codex compaction request for a model-source model
  is refused with a non-retryable client error instead of entering the
  subscription compact flow.

## Impact

Affected code: the Codex Responses route handler, the shared compact route
handler, and the model-source denial helpers in `app/modules/proxy/api.py`.
No dependency, database, configuration, or deployment changes.
