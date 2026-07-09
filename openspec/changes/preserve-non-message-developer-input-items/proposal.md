# Preserve Non-Message Developer Input Items

## Summary

Stop dropping non-message `input` items with a `system`/`developer` role when hoisting instruction messages into the `instructions` field.

## Motivation

Codex clients in responses-lite mode (gpt-5.6 models, which set `use_responses_lite=true` and `tool_mode=code_mode_only` in their model metadata) no longer send a top-level `tools` array. Instead, tool definitions are delivered as the first `input` item:

```json
{"type": "additional_tools", "role": "developer", "tools": [{"type": "custom", "name": "exec"}, ...]}
```

The instruction-hoisting normalizer treats every `system`/`developer`-role input item as an instruction message. `additional_tools` items carry no `content`, so they contributed no instruction text, produced no preserved item, and were silently removed from `input`. Upstream then received a well-formed request with no tools anywhere, and the model responded that no terminal/filesystem tool was exposed — every gpt-5.6 session through codex-lb was effectively toolless, while gpt-5.5 (classic top-level `tools`) kept working.

## Scope

- Only hoist input items that are actual messages (`type` omitted or `"message"`) into `instructions`.
- Pass every other `system`/`developer`-role input item through to upstream untouched, byte-for-byte.
- Applies to both `ResponsesRequest` and `ResponsesCompactRequest` normalization.

## Out of Scope

- Changing how message-shaped instruction items are hoisted or merged.
- Modeling the `additional_tools` item shape; it is forwarded opaquely to stay codex-faithful.
