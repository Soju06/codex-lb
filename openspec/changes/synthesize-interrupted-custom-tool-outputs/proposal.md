# Synthesize Interrupted Custom Tool Outputs

## Summary

Extend the interrupted-tool-output machinery — pending-call tracking, synthetic output injection, and the missing-tool-output error classifier — from `function_call` only to also cover `custom_tool_call` and `apply_patch_call`.

## Motivation

Implements #1168. When a turn ends with an unresolved `custom_tool_call` (e.g. the user interrupts Codex before the shell output is sent) and the next request references that response, upstream rejects it with:

```
{"type":"error","error":{"type":"invalid_request_error","message":"No tool output found for custom tool call call_xxx","param":"input"},"status":400}
```

Three sites only handled `function_call`: the pending-call tracker never recorded custom/apply-patch completions, the synthetic interrupted-output injection only emitted `function_call_output` items, and the error classifier only matched the function-call message variant — so the raw upstream 400 bypassed the existing masking/retry recovery and leaked to the client, permanently wedging the session. Reachable in practice since Responses-Lite custom shell tools started working (#1161); gpt-5.6 models emit `custom_tool_call` for every shell command.

## Scope

- Track pending tool calls as `call_id → call item type` for `function_call`, `custom_tool_call`, and `apply_patch_call`.
- Synthesize the matching output item type (`function_call_output` / `custom_tool_call_output` / `apply_patch_call_output`) when injecting interrupted outputs.
- Classify the custom and apply-patch variants of the upstream "No tool output found" message so existing recovery paths engage.
- Regression coverage at the websocket surface (interrupted custom/apply-patch turn → next request with `previous_response_id` → synthetic outputs injected) and the HTTP-bridge surface (custom tool call completions are recorded as pending).

## Out of Scope

- Adding a new interrupted-output injection site to the HTTP bridge (it records pendings but has no injection path today; recovery there is via the extended classifier).
- Anchor validation for stored responses ending in unanswered tool calls (tracked separately in #1167).
