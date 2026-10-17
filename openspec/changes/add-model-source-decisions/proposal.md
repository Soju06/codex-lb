## Why

Operators run decision models (for example `clef-flash` served by vLLM) behind
OpenAI-compatible endpoints and want to reach them through the proxy with the
same API keys, source restrictions, limits, and request logs as their chat and
embeddings traffic. The proxy exposes no route for either decision surface:
OpenAI's `POST /v1/decisions` and vLLM's structured-decision extension
`POST /v1/systemone`. As with embeddings, no subscription-backed upstream can
serve these requests, so they can only ever be served by a configured model
source.

## What Changes

- Add persisted `supports_decisions` and `supports_systemone` capability flags
  to model sources, both defaulting to disabled so existing sources keep their
  current behavior. The flags are independent: a source may serve the OpenAI
  shape, the vLLM shape, or both.
- Expose the flags through the model-source create/read/update API contracts
  and the dashboard model-source form.
- Add `POST /v1/decisions` and `POST /v1/systemone`, each routed only to an
  enabled source that declares the matching capability for the requested
  model, with no subscription-account fallback.
- Require `model` on `/v1/systemone` requests: vLLM treats it as optional, but
  the proxy needs it as the routing key.
- Account decision responses from their Responses-style `usage` object and
  record request logs with the same success/error and settlement rules as the
  embeddings route, including failing closed when a limited API key needs
  usage the source did not report.
- Forward the request payload verbatim beyond the validated fields, preserving
  explicit nulls and omissions.
- Deny both paths before body parsing on transports that disallow JSON bodies,
  matching the existing embeddings handling.
- Deny a required capability carried only in `client_metadata` on all three
  source-only routes (embeddings included), matching the Responses HTTP paths.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-source-routing`: two new capability flags and the routing,
  accounting, and field-presence contracts for the two decision routes.

## Impact

The change adds two boolean columns with a `false` server default, two proxy
routes, and two forwarding entry points. The embeddings route and forwarding
helper are generalized to share one implementation with the decision routes;
embeddings behavior and error text are unchanged. It adds no setting and no
dependency. Sources that do not opt in are unaffected.
