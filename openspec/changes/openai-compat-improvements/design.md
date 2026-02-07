## Context

codex-lb proxies OpenAI-compatible `/v1/responses` and `/v1/chat/completions` to a Codex upstream. The upstream expects list-based Responses inputs and a limited tool set, which currently causes mismatches and unclear errors for string inputs, chat multimodal content parts, and unsupported parameters. We need deterministic normalization and explicit unsupported errors, plus clearer documentation and live checks.

## Goals / Non-Goals

**Goals:**
- Normalize Responses string input into list-based `input_text` items before upstream.
- Map Chat multimodal content parts (text, image_url, input_audio, file) into Responses-compatible content parts.
- Best-effort proxy of image URLs into data URLs to avoid upstream download failures.
- Explicitly reject unsupported fields/tools (file_id, previous_response_id, truncation, built-in tools) with OpenAI-style invalid_request_error on `/v1/*`.
- Add a support matrix to the compatibility plan doc and expected/unsupported output in the live check script.

**Non-Goals:**
- Implementing file upload endpoints or file_id persistence.
- Adding support for web/file search, code interpreter, computer use, or image generation tools.
- Implementing conversation state for previous_response_id.

## Decisions

1. **Normalize Responses input strings in request validation**
   - Convert `input: "..."` into `[{role: "user", content: [{type: "input_text", text: "..."}]}]` inside `ResponsesRequest` so all upstream calls use list inputs.
   - Rationale: Aligns with upstream constraints and preserves API compatibility.

2. **Map Chat multimodal content parts in message coercion**
   - Convert chat `text` → `input_text`, `image_url` → `input_image`, `input_audio` → `input_file` (data URL), `file` → `input_file` (data URL or explicit error for file_id).
   - Rationale: Avoids upstream errors from unsupported content types and provides consistent behavior across Chat and Responses routes.

3. **Explicitly reject unsupported fields and tools at validation time**
   - Reject `previous_response_id`, `truncation`, `input_file.file_id`, and built-in tool types (web_search, file_search, code_interpreter, computer_use_preview, image_generation).
   - Rationale: Provide clear, stable invalid_request_error responses instead of upstream server_error.

4. **Best-effort image URL proxying before upstream**
   - Attempt to fetch `input_image.image_url` HTTP(S) resources, convert to data URLs with size limits and timeouts. If fetch fails, keep the original URL to preserve backward compatibility and avoid blocking requests.
   - Rationale: Improve success rate for public URLs while avoiding breaking changes for edge cases.

5. **Document support matrix and align live check output**
   - Add a supported/unsupported table to `refs/openai-compat-test-plan.md`.
   - Extend `scripts/openai_compat_live_check.py` to print expected unsupported features and compare results.

## Risks / Trade-offs

- **[Risk] Larger payloads due to data URLs** → Mitigation: enforce size limits (e.g., 8MB) and timeouts.
- **[Risk] External fetch latency** → Mitigation: use short timeouts and best-effort fallback to original URL.
- **[Risk] Audio/file data URL acceptance by upstream** → Mitigation: detect and surface clear invalid_request_error if upstream rejects.
- **[Risk] Behavior divergence for non-/v1 routes** → Mitigation: keep normalization in shared request handling and document differences.

## Migration Plan

1. Update validation and normalization in core request handling.
2. Add image URL proxying in upstream client path.
3. Update live check script and compatibility plan documentation.
4. Expand integration tests and run suite.
5. Deploy; rollback by reverting change if upstream rejects new mappings.

## Open Questions

- Should we introduce a lightweight file upload proxy to fully support file_id?
- Should image URL proxying be gated by a configuration flag?
