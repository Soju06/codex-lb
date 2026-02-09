## Context

codex-lb currently proxies `/v1/responses` and `/v1/chat/completions` to the ChatGPT backend via `/codex/responses`, with partial request validation and a best-effort mapping layer. The codebase already treats Responses as the internal wire and derives Chat Completions from streamed responses, but coverage is incomplete and some OpenAI fields are ignored or loosely validated. The backend is the limiting factor (no access to ChatGPT internals), so compatibility must be achieved via strict request/response handling, normalization, and explicit errors for unsupported features.

## Goals / Non-Goals

**Goals:**
- Make `/v1/responses` fully compatible with OpenAI request validation, response payloads, error envelopes, and SSE event semantics within backend limits.
- Make `/v1/chat/completions` fully compatible with OpenAI behavior by mapping to/from Responses (tools, response_format, usage, streaming chunks).
- Enforce strict, typed validation without speculative fallbacks; unsupported features must return OpenAI-style errors.
- Add wire-level compatibility tests using the official OpenAI client and the Codex client in `./refs/codex`.

**Non-Goals:**
- Implement OpenAI features the backend cannot provide (e.g., unsupported modalities, storage, or tooling) beyond explicit erroring.
- Build new upstream model providers or replace the ChatGPT backend.
- Introduce persistent storage or long-term conversation state beyond existing request logs.

## Decisions

1. **Responses as canonical internal wire**
   - Use Responses API schemas and SSE events as the single internal representation; Chat Completions are derived from Responses.
   - Rationale: avoids duplication and keeps streaming semantics consistent with existing upstream behavior.
   - Alternative: maintain separate chat/response pipelines. Rejected due to divergence risk and higher maintenance.

2. **Strict validation with typed models**
   - Keep Pydantic models as the contract; accept extra fields but validate required fields, types, and mutually exclusive options.
   - Unsupported but present fields that change behavior must return OpenAI-style errors, not be silently ignored.
   - Alternative: permissive parsing. Rejected to avoid undefined behavior and incompatibility with official clients.

3. **SSE normalization layer**
   - Normalize upstream SSE events into OpenAI Responses event shapes before downstream processing.
   - Chat streaming uses the normalized Responses stream to emit `chat.completion.chunk` events.
   - Alternative: parse upstream events directly for each API. Rejected to prevent inconsistent handling and duplicated logic.

4. **Error mapping and explicit limitations**
   - Map upstream failures and unsupported features into OpenAI error envelopes with stable `type`, `code`, and `param`.
   - For unsupported requests, return 400/422 with explicit error codes rather than 501.
   - Alternative: generic 500/501 errors. Rejected because official clients expect specific error fields.

5. **Usage accounting from upstream**
   - Use upstream usage fields (`input_tokens`, `output_tokens`, etc.) when present and map to both Responses and Chat usage schemas.
   - Log usage in request logs without backfilling or recomputing tokens.
   - Alternative: local token counting. Rejected due to drift and model-specific variance.

6. **Compatibility test harness**
   - Create a shared compatibility test suite that executes requests via the official OpenAI client and the Codex client against the local endpoint and validates wire-level parity.
   - Alternative: unit-only tests. Rejected because full compatibility depends on runtime streaming behavior.

## Risks / Trade-offs

- **[Backend feature gaps]** → Mitigation: explicit error codes with clear messages; document limitations in specs.
- **[Streaming event drift]** → Mitigation: SSE normalization and fixture-based stream tests.
- **[Strict validation breaks permissive clients]** → Mitigation: align validation strictly with OpenAI docs; allow unknown fields unless they alter behavior.
- **[Official client changes]** → Mitigation: centralize schema mapping and update tests as part of releases.

## Migration Plan

- Implement compatibility layers behind existing endpoints (no new routes).
- Add unit + integration + streaming tests before enabling stricter validation in production.
- Roll out in stages (canary if available), monitor request logs for new error patterns.
- Rollback by reverting validation/normalization changes; endpoints remain stable.

## Open Questions

- Which OpenAI Responses features are unsupported by the ChatGPT backend and must be explicitly blocked?
- Should `/v1/responses` support non-streaming responses (aggregate server-side) or remain streaming-only?
- How should structured output (`response_format` / `text.format`) validation align with backend capabilities?
- Is `responses/compact` in scope or explicitly out-of-scope for this change?
