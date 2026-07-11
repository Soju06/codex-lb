## Why

The upstream compact endpoint now rejects compact payloads when
`parallel_tool_calls` is missing. codex-lb previously removed that field along
with tool-only request fields, which breaks compaction for clients that rely on
the proxy's request normalization.

## What Changes

- Keep stripping compact-incompatible tool definitions from compact requests.
- Force compact upstream payloads to include `parallel_tool_calls: false`.
- Update tests and specs so the compact contract is explicit and non-conflicting.

## Impact

- Compact requests continue to avoid forwarding unsupported `tools` and
  `tool_choice` fields.
- Upstream compact requests include the required `parallel_tool_calls` value.
