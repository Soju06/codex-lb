# Why

`/v1/chat/completions` maps onto Responses. `ChatCompletionsRequest.tools`
uses `default_factory=list`, and `to_responses_request()` always writes
`tools` onto the converted request. That marks the field as set, so
`ResponsesRequest.to_payload()` forwards a synthesized `"tools": []` the
client never sent. The Responses and `/v1/responses` omit path already
avoids this (issue #1184). Chat still does not.

# What Changes

- Propagate chat `tools` omission through `to_responses_request()`.
- Keep an explicit client-sent `[]` on the mapped Responses payload.
- Leave source-routed chat sanitization as a second, independent omit.

# Capabilities

### Modified Capabilities

- `chat-completions-compat`: omitted chat `tools` must stay omitted on the
  mapped Responses wire payload.

# Impact

Source-routed chat already drops empty `tools` in
`sanitize_source_chat_payload`. Codex-backend chat inherits the same omit
rule as `/v1/responses`. Explicit tools, including `[]`, stay intact.
