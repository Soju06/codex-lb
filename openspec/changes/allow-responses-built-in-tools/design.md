## Context

`app/core/openai/requests.py` exposes `validate_tool_types`, a shared Pydantic field validator used by the Responses request model, the v1 Responses request model, and the Chat Completions request model. Today that validator rejects the same `UNSUPPORTED_TOOL_TYPES` set for every caller: `file_search`, `code_interpreter`, `computer_use`, `computer_use_preview`, `image_generation`. Newer Codex clients started sending `image_generation` (and may send other Responses built-ins) against `/backend-api/codex/responses` and WebSocket `response.create`, which the LB rejects with `invalid_request_error` before upstream even sees the request.

Chat Completions has a narrower contract (see `chat-completions-compat`): only `web_search` and `web_search_preview` are allowed, and other built-ins are explicitly rejected. That rejection is still the right behavior for `/v1/chat/completions` clients.

## Goals / Non-Goals

**Goals:**
- Treat Responses-family tool policy as pass-through: validate only payloads the proxy cannot represent, and leave tool support to upstream.
- Keep Chat Completions tool policy unchanged so existing OpenAI-compat clients keep getting clear 4xx errors for unsupported built-ins.
- Cover HTTP (`/v1/responses`, `/backend-api/codex/responses`) and WebSocket transports with the same relaxed policy.
- Preserve the existing `web_search_preview -> web_search` alias normalization.

**Non-Goals:**
- Adding proxy-side understanding of built-in tool outputs (events, stream item types, include allowlist entries). Upstream remains the source of truth for those.
- Widening Chat Completions tool support in this change.
- Changing unrelated Responses validators (store, truncation, previous_response_id handling, etc.).

## Decisions

1. **Split the unsupported-tool set by contract, not by call site.**
   - `RESPONSES_UNSUPPORTED_TOOL_TYPES` is an explicit (initially empty) frozenset for Responses-family requests.
   - `CHAT_UNSUPPORTED_TOOL_TYPES` preserves the existing Chat Completions rejection list.
   - Rationale: the policy difference is between OpenAI surfaces, not between endpoints. Encoding it as two named constants keeps intent visible and makes future adjustments (e.g., Chat relaxing its own list) additive rather than accidentally changing the other surface.
   - Alternative: per-endpoint allowlists. Rejected because we already share one validator and drift between Responses entry points was the original motivation for centralizing it.

2. **Parameterize `validate_tool_types` with a default that matches Responses-family behavior.**
   - `validate_tool_types(..., *, unsupported_tool_types: frozenset[str] = RESPONSES_UNSUPPORTED_TOOL_TYPES)`.
   - Rationale: `V1ResponsesRequest` and `ResponsesRequest` get the relaxed default for free, matching how they are wired up today. Only `ChatCompletionsRequest` needs to opt into the stricter set.
   - Alternative: two helper functions (`validate_responses_tool_types` / `validate_chat_tool_types`). Rejected as unnecessary surface area; the predicate is identical, only the set differs.

3. **Keep tool alias normalization unchanged.**
   - `web_search_preview -> web_search` continues to happen for all tool-bearing requests.
   - Rationale: alias handling is about payload shape, not policy, and upstream still rejects `web_search_preview`.

4. **Do not expand `_RESPONSES_INCLUDE_ALLOWLIST` here.**
   - Rationale: `include` values are a separate contract. If clients start sending `include=["image_generation_call.partial_images"]` or similar and upstream accepts them, that is its own change with its own validation updates.

## Risks / Trade-offs

- **[Risk] Upstream might reject a newly accepted built-in with a less friendly error.** Mitigation: by design upstream is now the authority. The user-visible effect is the same class of 4xx with the actual upstream message, which is better than a stale LB-side rejection that ignores the real reason.
- **[Risk] A future Responses built-in legitimately needs proxy-side handling.** Mitigation: adding it back to `RESPONSES_UNSUPPORTED_TOOL_TYPES` is a single-line change and still surfaces the existing OpenAI invalid_request_error envelope.
- **[Trade-off] Two sets instead of one.** Minor duplication, but it makes the Responses vs Chat policy split explicit and prevents a Chat relaxation from silently leaking through the shared validator.

## Migration Plan

1. Split the constant and parameterize `validate_tool_types`; keep Responses validators calling the helper with the default argument.
2. Update `ChatCompletionsRequest._validate_tools` to pass `CHAT_UNSUPPORTED_TOOL_TYPES` explicitly.
3. Update Responses-family tests to assert that the previously rejected built-ins are now forwarded to upstream unchanged (HTTP + WebSocket).
4. Keep the Chat Completions integration test (`test_v1_chat_completions_rejects_builtin_tools`) as-is.
5. Deploy; rollback is reverting this change, which restores the previous strict Responses rejection.

## Open Questions

- Should we eventually relax Chat Completions the same way? Deferred: that decision belongs with whoever owns the Chat compat contract and its clients.
