## Why

HTTP request bodies without content encoding are currently unbounded, while compressed bodies are read fully into memory before the existing decompressed-size guard runs. A client can therefore force excessive per-request memory growth before authentication or route handling, and compression failures on proxy paths currently use the dashboard error envelope instead of the documented OpenAI-compatible envelope.

## What Changes

- Enforce the existing HTTP body budgets against raw request bytes while ASGI chunks are received, before oversized chunks are exposed to downstream body parsing.
- Keep the larger existing budget for `/v1/responses` and `/backend-api/codex/responses`, including trailing-slash variants; use the general existing budget for other HTTP paths.
- Register hidden trailing-slash HTTP aliases for both Responses paths so FastAPI does not redirect before chunked-body admission runs.
- Preserve decompressed-size enforcement for `gzip`, `deflate`, `zstd`, `identity`, and stacked encodings so neither transfer size nor expanded size can exceed its route budget.
- Reject oversized bodies with HTTP 413 and path-appropriate error envelopes; return OpenAI-compatible invalid-request envelopes for malformed or unsupported compression on proxy path families.
- Leave requests declaring unencoded `multipart/form-data` outside this generic whole-body limit so multipart per-file and aggregate limits can remain a separate, standalone change.
- Add no new setting: the guard reuses `max_decompressed_body_bytes` and `max_decompressed_responses_body_bytes`.

## Capabilities

### New Capabilities

- `http-ingress-limits`: Defines bounded raw and decompressed HTTP request ingestion, route budgets, multipart scope, disconnect behavior, authorization handoff, and path-appropriate rejection envelopes.

### Modified Capabilities

- `responses-api-compat`: Defines the Responses HTTP ingress budget and OpenAI-compatible oversized-request envelope for both canonical response paths.

## Impact

- Affected code: HTTP ingress middleware, request decompression, shared error-envelope construction, application middleware registration, exception handling, and the two Responses HTTP route registrations.
- Affected APIs: HTTP request parsing on dashboard and proxy routes; no websocket behavior, schema, database, dependency, new setting, or default change. The two existing decompressed-body settings also become the raw-ingress budgets for guarded requests.
- Oversized requests that were previously allowed to consume unbounded memory will fail locally before route logic or upstream forwarding.
