# Set `parallel_tool_calls` to False on Compaction

## Background
The upstream Responses-Lite endpoint (`/responses/compact`) previously accepted payloads with the `parallel_tool_calls` key removed. It now strictly rejects requests where the key is missing, explicitly requiring `parallel_tool_calls: false`.

## Specification Changes
Modifies the responses API compatibility requirements.

* When processing a request for `/backend-api/codex/responses/compact` or `/v1/responses/compact`, the service SHALL NOT remove `parallel_tool_calls`.
* The service SHALL explicitly set `parallel_tool_calls` to `false` before calling the upstream endpoint.