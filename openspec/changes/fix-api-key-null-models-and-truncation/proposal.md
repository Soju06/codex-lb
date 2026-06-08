# fix-api-key-null-models-and-truncation

## Summary

Fix two compatibility edge cases:

- API key `allowed_models` deserialization must ignore JSON `null` and other non-string entries instead of converting them into model names.
- Responses requests may include OpenAI's `truncation` control; codex-lb should accept the documented values and omit the field before forwarding to the ChatGPT-backed upstream path.

## Motivation

Users can hit false `model_not_allowed` 403 responses when stored `allowed_models` data contains JSON nulls, because `null` was deserialized as the string `"None"`.

VS Code/Copilot custom endpoint traffic can include `truncation: "auto"`, which codex-lb rejected before account selection even though the field can be safely treated as a compatibility hint for the downstream OpenAI-compatible surface.

## Scope

- Backend request/model normalization only.
- No database schema changes.
- No frontend changes.
