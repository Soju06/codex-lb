# Preserve Responses Tools Wire Shape

## Summary

Stop synthesizing an unset top-level `tools: []` field and stop reordering
client-supplied top-level tools while forwarding Responses requests.

## Motivation

Responses Lite carries its tool bundle in the `additional_tools` input item and
deliberately omits the top-level `tools` field. `ResponsesRequest` currently
defaults `tools` to an empty list and serializes that default, so the proxy adds
`"tools": []` even though the client never sent it. GPT-5.6 reconciles an
explicit top-level tools parameter with reserved model tools such as
`collaboration.spawn_agent`; the synthesized empty list fails that check with a
400 `param: "tools"` error (issue #1184).

The same serialization path sorts the top-level tool list and recursively sorts
each tool object's keys. Reserved tool schemas must reach upstream in the
client-provided structure and order rather than a proxy-canonicalized wire
shape.

## Scope

- Distinguish an omitted `tools` field from an explicitly supplied empty list.
- Preserve that distinction through OpenAI-compatible conversion and
  multi-instance HTTP bridge owner forwarding.
- Omit unset tools on native HTTP, websocket, HTTP-bridge, owner-forward, and
  model-source Responses egress paths.
- Preserve explicit tool list order and nested object key order on the wire
  after existing route-specific compatibility filtering.
- Retain order-insensitive canonicalization only on a detached copy used for
  the request-shape observability hash.
- Add unit and product-path regressions for Responses Lite and explicit
  reserved tools.

## Out of Scope

- Changing compact request behavior, which deliberately removes top-level
  tools and forces `parallel_tool_calls = false`.
- Removing the intentional `store: false` or `include: []` defaults.
- Changing chat-completions tool conversion or model-reserved tool schemas.
