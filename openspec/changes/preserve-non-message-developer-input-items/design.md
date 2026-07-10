# Design

## Approach

Add a type guard to `_normalize_responses_input_instructions()` in
`app/core/openai/requests.py`: before treating a `system`/`developer`-role
input item as an instruction message, check the item's `type`. Items whose
`type` is present and not `"message"` are appended to the preserved input list
unchanged, regardless of role.

Typeless role items (`{"role": "system", "content": ...}`, as sent by
OpenAI-compatible clients) keep the existing hoisting behavior, so `/v1`-style
compatibility is unaffected. The guard lives in the shared normalizer, so both
`ResponsesRequest` and `ResponsesCompactRequest` validators pick it up, and the
compact `to_payload()` path (which re-runs the normalizer via
`_strip_compact_unsupported_fields`) applies the same rule.

The Responses Lite `additional_tools` early return stays in front of the loop:
when a Lite tool bundle is present, the entire input array (including the
adjacent developer instructions message) is deliberately left untouched so the
native Codex wire shape reaches upstream byte-for-byte. The per-item type guard
is the general safety net for every other non-message typed item.
