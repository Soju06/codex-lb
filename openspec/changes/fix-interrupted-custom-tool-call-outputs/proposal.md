# Fix interrupted custom tool call output synthesis

## Why

The interrupted-tool-output machinery only handles `function_call` items. When
a turn ends with an unresolved `custom_tool_call` (or `apply_patch_call`) and
the next request references that response id, upstream rejects the follow-up
with `400 "No tool output found for custom tool call call_..."`. Three sites
are `function_call`-only today: the pending-call tracker, the synthetic
interrupted-output injection, and the `_is_missing_tool_output_error`
classifier that powers masking/retry recovery (issue #1168).

## What Changes

- Track completed `custom_tool_call` and `apply_patch_call` items (not just
  `function_call`) as pending tool calls, remembering each call's item type.
- Synthesize the matching output item type for interrupted pending calls
  (`function_call` -> `function_call_output`, `custom_tool_call` ->
  `custom_tool_call_output`, `apply_patch_call` -> `apply_patch_call_output`)
  when the next anchored request omits the output.
- Inject synthetic interrupted outputs on the HTTP responses bridge when a
  follow-up request anchors on the bridge session's last completed response,
  mirroring the direct WebSocket route.
- Extend the missing-tool-output error classifier to match the custom tool
  call and apply patch call message variants so masking/retry recovery
  engages instead of leaking the raw upstream 400.

## Impact

- Affected specs: `responses-api-compat`
- Affected code: `app/modules/proxy/_service/response_create.py`,
  `app/modules/proxy/_service/support.py`,
  `app/modules/proxy/_service/websocket/{mixin,helpers}.py`,
  `app/modules/proxy/_service/http_bridge/{upstream_events,streaming,service_stubs}.py`,
  `app/modules/proxy/service.py`
