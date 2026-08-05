# Classify the tool-search missing-tool-output rejection

## Why

Upstream rejects an anchored Responses request whose input contains a
`tool_search_call` without its matching `tool_search_output` with:

```json
{"type":"error","status":400,
 "error":{"type":"invalid_request_error",
          "message":"No tool output found for tool search call call_xxx",
          "param":"input"}}
```

`_MISSING_TOOL_OUTPUT_MESSAGE_PREFIXES` in `app/modules/proxy/service.py`
enumerates only the `function call`, `custom tool call`, and `apply patch call`
wordings, so `_is_missing_tool_output_error` returns `False` for the tool-search
wording. The raw upstream 400 is then forwarded verbatim to the client, the
continuity fail-closed counter is never recorded, and on a multiplexed HTTP
bridge or WebSocket session the anonymous error event is no longer preferentially
matched to the request that carries `previous_response_id`, so it can be
attributed to an unrelated pending request on the same socket.

This is the same enumeration gap #1168 fixed for the custom-tool and
apply-patch wordings; `tool_search_call` / `tool_search_output` are already
modelled elsewhere in the proxy (`app/modules/proxy/replay_safety.py`), so the
item type is real, only the classifier list is stale.

## What Changes

- Add the `No tool output found for tool search call call_` prefix to the
  missing-tool-output classifier so the existing masking, continuity
  fail-closed recording, and anonymous-event matching engage for the
  tool-search wording exactly as they do for the other three.
- Keep the `web search call` wording unclassified: `web_search_call` is a
  hosted, server-executed item with no `call_id`-addressed client output, so
  there is nothing for the continuity recovery paths to do with it.
- Add regression coverage for the classifier and for the HTTP-bridge masking
  surface.

## Non-goals

- Synthesising an interrupted `tool_search_output` item.
  `_PENDING_TOOL_CALL_OUTPUT_ITEM_TYPE_BY_CALL_TYPE` is deliberately left
  unchanged: `tool_search_output` does not have the
  `{"output": "<text>"}` shape that
  `_synthetic_interrupted_function_call_output` emits (it carries a `tools`
  list), so extending the synthesiser would require asserting an upstream
  payload shape this repo cannot verify, and a wrong shape would turn a
  maskable 400 into a different, unmaskable one.

## Issue Trace

- Refs #1168

## Impact

- **Spec**: `responses-api-compat`
- **Behavior**: the tool-search missing-tool-output 400 is masked as a
  retryable continuity failure instead of leaking upstream's raw message and
  call id downstream.
- **Persistence/UI**: no database, migration, configuration, or dashboard
  changes.
