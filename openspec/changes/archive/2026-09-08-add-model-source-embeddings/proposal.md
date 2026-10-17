## Why

Model sources already carry per-protocol capability flags for chat
completions, responses, and audio transcriptions, and the proxy routes each
OpenAI-compatible surface to a source that declares the matching capability.
Embeddings have no such flag and no route, so an operator running a local
embedding model behind an OpenAI-compatible source cannot serve
`POST /v1/embeddings` through the proxy at all. There is also no
subscription-backed upstream to fall back on: unlike chat and responses
traffic, embeddings can only ever be served by a configured model source.

Adding the capability requires a persisted flag, so the create/read/update
contracts, the dashboard form, request validation, and request-log accounting
all move together.

## What Changes

- Add a persisted `supports_embeddings` capability flag to model sources,
  defaulting to disabled so existing sources keep their current behavior.
- Expose the flag through the model-source create/read/update API contracts
  and the dashboard model-source form.
- Add `POST /v1/embeddings`, routed only to an enabled source that declares
  the embeddings capability for the requested model.
- Return `model_not_found` when no enabled source supports embeddings for the
  requested model, instead of falling through to subscription accounts.
- Record embeddings requests in the request log with the same success/error
  accounting and usage-settlement rules the other model-source routes use,
  including failing closed when a limited API key needs usage the source did
  not report.

## Capabilities

### New Capabilities

- `model-source-routing`: the embeddings capability flag, its routing rule,
  and the `/v1/embeddings` request/accounting contract.

### Modified Capabilities

None.

## Impact

The change adds one nullable-free boolean column with a `false` server
default, one proxy route, one forwarding helper, and one repository lookup.
It adds no setting, no dependency, and no change to existing routing for chat
completions, responses, or audio transcriptions. Sources that do not opt in
are unaffected.
