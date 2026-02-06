## Context

codex-lb proxies OpenAI-compatible `/v1/responses` and `/v1/chat/completions` to a Codex upstream. The current tool validation rejects all built-in tools, including web_search, which causes 422 errors on both `/v1/*` and `/backend-api/codex/*`. We need to allow web_search while keeping the existing guardrails for other unsupported built-in tools.

## Goals / Non-Goals

**Goals:**
- Allow web_search tools across Responses and Chat Completions endpoints.
- Keep blocking for unsupported built-in tools (file_search, code_interpreter, computer_use, image_generation).
- Ensure the same validation policy is applied to `/backend-api/codex/responses` and `/v1/*`.
- Preserve existing tool normalization for function tools.

**Non-Goals:**
- Implementing support for other built-in tools or tool outputs.
- Changing upstream endpoints or adding new proxy routes.

## Decisions

1. **Centralize tool validation + normalization**
   - Use a shared helper to validate tool types and normalize web_search aliases.
   - Rationale: ensures consistent behavior across Responses and Chat requests and avoids endpoint-specific exceptions.
   - Alternative: per-endpoint allowlists. Rejected due to drift risk.

2. **Allow web_search and web_search_preview; block the rest**
   - Accept `web_search` and `web_search_preview` in tool definitions.
   - Continue rejecting `file_search`, `code_interpreter`, `computer_use(_preview)`, and `image_generation` with invalid_request_error.
   - Rationale: matches product intent while preserving guardrails.

3. **Normalize web_search_preview -> web_search for upstream**
   - Convert `web_search_preview` tool types to `web_search` before upstream call.
   - Rationale: codex upstream rejects `web_search_preview`; normalization keeps client compatibility.
   - Alternative: pass through unchanged and rely on upstream compatibility. Rejected due to observed upstream errors.

4. **Chat tool normalization preserves built-in tools**
   - Update chat tool normalization to pass through web_search tools (after normalization) instead of dropping them.
   - Rationale: chat requests should behave consistently with Responses and not silently discard tools.

## Risks / Trade-offs

- **[Risk] Upstream behavior changes for web_search** → Mitigation: keep mapping limited to web_search tools; surface upstream error in OpenAI envelope.
- **[Risk] Tool payload shape drift** → Mitigation: avoid over-normalizing fields; only adjust tool type.

## Migration Plan

1. Update tool validation/normalization helpers and apply to Responses + Chat request models.
2. Adjust chat tool normalization to preserve web_search tools.
3. Update integration tests and live check expectations.
4. Deploy; rollback by reverting change if upstream rejects web_search tools.

## Open Questions

- Should we add a configuration flag to disable web_search forwarding if upstream changes behavior?
