# Verification

## Regression evidence

Before adding the selector, the focused regression selection reported **26 failed,
29 passed**. The failures reproduced rejection of `output.encrypted_content`
through request models, HTTP, WebSocket and the OpenAI SDK. Adding the selector
made the same selection pass: **55 passed**. The request-model baseline is
identical at upstream commit `72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e`.

The only production change is the new allowlist entry. Existing include
validation and output serializers are unchanged.

## Contract coverage

- Both native and public HTTP paths, including trailing slashes, JSON and SSE.
- Both WebSocket paths, with the existing trailing-slash rejection retained.
- Exact preservation of synthetic message, function-call, custom-tool,
  web-search, reasoning and compaction fields through output and the next input.
- Public terminal-output backfill and existing native SSE item-event semantics.
- Unknown selectors, null arrays and non-string include entries remain invalid.
- Real OpenAI SDK parsing and replay against the local ASGI application.
- Message and tool-call ciphertext remains account-scoped after item-ID
  projection. Removing ciphertext from the same projected item is the positive
  control for the account-neutral classifier.

## Static and specification validation

`make lint` and `uv run ty check` pass on the isolated upstream-based candidate.
With CI's pinned OpenSpec 1.11.0, all 67 canonical specs pass strict validation.
The change also passed strict validation before archive, and its requirements
and context are synced to `responses-api-compat`.

The PR test plan records the broader final test run and full local CI result.
Tests use isolated databases, synthetic ciphertext and mocked upstream
transport. They establish validation and field preservation, not provider-side
cryptographic validity. No live model calls or deployment are part of these
checks.
