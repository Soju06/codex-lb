# Design

## Approach

Add a type guard to `_normalize_responses_input_instructions()` in `app/core/openai/requests.py`: before treating a `system`/`developer`-role input item as an instruction message, check the item's `type`. Items whose `type` is present and not `"message"` are appended to the preserved input list unchanged.

Typeless role items (`{"role": "system", "content": ...}`, as sent by OpenAI-compatible clients) keep the existing hoisting behavior, so `/v1`-style compatibility is unaffected. The guard lives in the shared normalizer, so both `ResponsesRequest` and `ResponsesCompactRequest` validators pick it up.

This keeps the codex-faithful wire format: native Codex responses-lite bodies reach upstream with their `additional_tools` prefix intact, exactly as the Codex CLI emits them.
