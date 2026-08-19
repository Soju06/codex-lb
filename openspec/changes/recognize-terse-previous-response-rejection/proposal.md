## Why

Upstream detection of an unresolvable `previous_response_id` is string-shaped: `is_previous_response_not_found_error` requires `code=previous_response_not_found`, or `code=invalid_request_error` **with** `param=previous_response_id` **and** a message containing "previous response … not found".

The Codex backend now rejects an unresolvable anchor with a terse `Invalid \`previous_response_id\`.` that carries neither the `param` nor the "not found" wording. The predicate stops matching, and every recovery site gated on it — the WebSocket full-resend retry, the HTTP bridge anchor rewrite, and the public-error masking — is skipped, so the raw upstream 400 reaches the client as a failed turn the user has to abandon.

Observed on a production deployment: anchor recoveries ran 1–8/hour and were invisible to users until the message shape changed, after which recoveries dropped to zero and raw 400s rose to 17/hour across 10 API keys and both transports (56 HTTP / 15 WebSocket in 24 hours).

## What Changes

- `is_previous_response_not_found_message` also matches a message that names `previous_response_id` alongside "invalid" (in addition to the existing "previous response … not found" wording).
- A missing error `param` is treated as inconclusive rather than disqualifying: only a *different* `param` rules the anchor out as the cause. `code` must still be `invalid_request_error` (or the canonical `previous_response_not_found`).
- No change to what recovery does. Retrying with full input and no anchor is already the correct response to any upstream rejection of the anchor itself, including a malformed one.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: upstream previous-response misses are detected from the terse rejection shape as well, so recovery and public-error masking keep engaging.
