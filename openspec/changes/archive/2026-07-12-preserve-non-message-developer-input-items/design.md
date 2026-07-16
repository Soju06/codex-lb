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

Preserving a directive counts as a normalization outcome (`changed = True`):
the normalizer then merges instructions as usual, which defaults the top-level
`instructions` field to `""` when the request carries none and no instruction
messages are hoisted. Without this, a directive-only input would leave the
payload untouched and fail validation on the required `instructions` field —
the pre-change path implicitly set `instructions` to `""` while dropping the
directive.

The Responses Lite `additional_tools` early return stays in front of the loop:
when a Lite tool bundle is present, the entire input array (including the
adjacent developer instructions message) is deliberately left untouched so the
native Codex wire shape reaches upstream byte-for-byte. The per-item type guard
is the general safety net for every other non-message typed item.

The preservation rule lives in a single predicate,
`_is_preserved_non_message_directive()` (system/developer role, `type` present
and not `"message"`), shared by three normalization stages:

- Instruction hoisting (`_normalize_responses_input_instructions()`) passes
  matching items through untouched.
- Input sanitization (`_sanitize_input_items()`) skips matching items so the
  interleaved-reasoning sanitizer — which targets assistant-message echo
  fields — cannot strip top-level `reasoning_content`, `reasoning_details`,
  `tool_calls`, or `function_call` keys (or reasoning-typed content parts)
  from a preserved directive. This cannot regress prior behavior: before this
  change, such items were dropped entirely, so nothing depended on their
  sanitized shape.
- Compact upstream trimming (`_compact_state_anchor_indices()`) treats
  matching items as trim anchors, so a preserved directive sitting in the
  trimmed middle of an oversized compact request is kept in place instead of
  being replaced by the trim marker. Existing anchors (`additional_tools`,
  compact state items, and reconciled tool calls) are unaffected.
