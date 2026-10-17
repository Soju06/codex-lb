## Why

The public Responses output filter drops `tool_search_output`, leaving clients
without the tool definitions returned by hosted discovery. The type matches
neither of the filter's `_call` and `_call_output` suffixes.

## What Changes

- Add `tool_search_output` to the existing output allowlist.
- Cover JSON and SSE output, including reconstruction from completed item events.

## Impact

Public Responses output only. For example, discovery output followed by a
function call will retain both items. Replay handling in #1952 is separate.

Protocol reference: https://developers.openai.com/api/docs/guides/tools-tool-search
