# Why

The upstream Responses websocket sends some request errors as pretty-printed,
multi-line JSON, for example the JSON-mode error:

```
{
  "type": "error",
  "error": {
    "type": "invalid_request_error",
    "code": null,
    "message": "Response input messages must contain the word 'json' in some form to use 'text.format' of type 'json_object'.",
    "param": "input"
  },
  "status": 400
}
```

The HTTP bridge frames each websocket text frame as an SSE `data:` field and
parses it with SSE rules, so only the first line (`{`) is treated as data. The
frame parses to nothing, the error never reaches the waiting request, and the
request waits for `response.created` until the idle timeout. The client gets a
502 after about a minute instead of the upstream 400 right away.

The direct websocket path already parses each frame as one JSON document, so it
is not affected.

# What Changes

- The HTTP bridge parses each upstream websocket text frame as one JSON
  document, including frames that span several lines.
- When such a frame is relayed, it is re-encoded as a single-line `data:` block,
  so the downstream SSE stream stays well-formed.
- Single-line frames keep the existing fast path and relay the upstream text
  unchanged.

# Capabilities

### Modified Capabilities

- `responses-api-compat`: the HTTP bridge parses multi-line upstream websocket
  frames.

# Impact

- Code: `app/core/utils/sse.py`,
  `app/modules/proxy/_service/http_bridge/upstream_events.py`
- Tests: SSE helpers, bridge frame processing, and `/v1/responses` over the
  HTTP bridge.
