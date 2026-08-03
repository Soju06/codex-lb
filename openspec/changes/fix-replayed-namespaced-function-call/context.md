# Replayed namespaced function-call compatibility

## Purpose

Newer Codex clients can replay historical side-effect calls with a local `namespace` such as `collaboration`. The proxy uses that namespace to distinguish same-named calls during deduplication, while the OpenAI upstream input schema accepts the historical call fields but rejects `namespace`.

## Decision and constraints

Treat the namespace as local metadata: retain it on the parsed request and remove it only from the copied wire payload. The rule applies only to `input` items of type `function_call`; top-level `tools` entries, other input-item types, and cross-account replay policy remain unchanged.

## Failure modes

- Forwarding the field causes an upstream `invalid_request_error` naming `input[*].namespace`.
- Removing it during parsing collapses local namespaced dedupe identity.
- Recursively removing every `namespace` key corrupts reserved top-level namespace tool definitions.

## Example

An input item `{ "type": "function_call", "namespace": "collaboration", "name": "spawn_agent", "arguments": "{}", "call_id": "call_123" }` remains intact for local processing. Its upstream copy is identical except that `namespace` is absent.
