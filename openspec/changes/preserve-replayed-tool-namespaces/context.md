# Context: Preserve replayed tool namespaces

## Purpose and scope

ChatGPT tool executors are identified by the pair `(namespace, name)`. The
proxy previously applied a compatibility sanitizer globally, even though the
ChatGPT Responses wire accepts and uses that identity. The reported failure
appeared after reconnect, compaction, or another long-history replay: the
history still contained a call name but no namespace by the time it reached
the upstream executor registry.

The generic model-source path is a separate compatibility surface. Its
existing test and upstream contract require removing the namespace from
recognized call items, so this change deliberately keeps that behavior there.

## Concrete example

For a replayed input item

```json
{"type":"custom_tool_call","namespace":"exec","name":"js","call_id":"c1","input":"pwd"}
```

the ChatGPT standard, Lite/compact, bridge, and WebSocket payloads retain
`namespace: "exec"`. A configured OpenAI-compatible source receives the same
item without `namespace`, while all other fields remain unchanged.

## Constraints

- Local replay classification must see the original namespace.
- Top-level namespace tool declarations must remain byte-preserved.
- No production browser action or model continuation is implied by serializer
  tests; those require a separately authorized live smoke test.
