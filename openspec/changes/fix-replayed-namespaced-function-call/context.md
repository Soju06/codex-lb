# Replayed namespaced tool-call compatibility

## Purpose

Newer Codex clients can replay historical `function_call`, `custom_tool_call`, and `apply_patch_call` items with a local `namespace` such as `collaboration` or `exec`. The proxy uses that namespace to distinguish same-named calls during deduplication, while the OpenAI upstream input schema accepts the historical call fields but rejects `namespace`.

## Decision and constraints

Treat the namespace as local metadata: retain it on the parsed request and replay-safety classifier input, then remove it only from copied wire payloads. The rule applies only to replayed tool-call `input` items; top-level `tools` entries, other input-item types, and cross-account replay policy remain unchanged. Configured model-source requests receive only this namespace normalization so their otherwise supported OpenAI-compatible fields are preserved.

## Failure modes

- Forwarding the field causes an upstream `invalid_request_error` naming `input[*].namespace`.
- Removing it during parsing collapses local namespaced dedupe identity.
- Recursively removing every `namespace` key corrupts reserved top-level namespace tool definitions.

## Example

An input item `{ "type": "custom_tool_call", "namespace": "exec", "name": "exec", "input": "git status", "call_id": "call_123" }` remains intact for local processing. Its upstream copy is identical except that `namespace` is absent.
