# Tasks

## 1. Specification

- [x] 1.1 Define presence-aware parameter parsing, fail-closed replay, public
      sanitization, and typeless/nested terminal-event requirements.
- [x] 1.2 Record scope boundaries: no new retry, account, durable-operation,
      circuit, quarantine, or container behavior.

## 2. Implementation

- [x] 2.1 Add the immutable `OpenAIErrorParam` state and preserve it through
      strict/fallback OpenAI error parsing.
- [x] 2.2 Split strict recovery classification from public stale-anchor shape
      matching; malformed present parameters never authorize replay.
- [x] 2.3 Sanitize malformed parameters and trim valid values in shared
      WebSocket, HTTP Responses, and Chat Completions serializers.
- [x] 2.4 Classify typeless error frames and preserve nested response ids while
      masking terminal stale-anchor errors.

## 3. Coverage

- [x] 3.1 Cover absent, valid, malformed, and whitespace parameter states and
      strict/public classifier differences.
- [x] 3.2 Cover typeless errors, nested `response.failed` masking, native
      malformed-parameter sanitization, and valid native byte preservation.
- [x] 3.3 Cover Chat Completions stale-anchor masking and malformed metadata.

## 4. Verification

- [x] 4.1 Run focused Responses, WebSocket, Chat Completions, and proxy utility
      unit suites.
- [x] 4.2 Run Ruff, formatting, `ty`, and `git diff --check` on the candidate.
- [x] 4.3 Run strict OpenSpec validation when the CLI is available; this
      candidate passed `pnpm --silent dlx @fission-ai/openspec@1.10.0
      validate harden-websocket-stale-anchor-error-shapes --strict`.
- [ ] 4.4 Obtain current-hosted CI, CodeRabbit, mergeability, and maintainer
      review for the exact pushed head before calling the PR merge-ready.
