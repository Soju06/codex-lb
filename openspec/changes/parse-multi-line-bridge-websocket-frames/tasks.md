## 1. Implementation

- [x] 1.1 Parse each upstream websocket text frame in the HTTP bridge as one
  JSON document, including multi-line frames.
- [x] 1.2 Re-encode a parsed multi-line frame as a single-line `data:` block
  before it is relayed.

## 2. Regression coverage

- [x] 2.1 A pretty-printed upstream error frame (LF, CRLF and CR line breaks)
  settles the waiting bridge request with the upstream error.
- [x] 2.2 `POST /v1/responses` over the HTTP bridge returns the upstream 400
  right away instead of waiting for the idle timeout.
- [x] 2.3 The frame parser decodes every JSON object frame and rejects
  non-object text.

## 3. Validation

- [x] 3.1 Run the bridge, SSE, and websocket tests.
- [x] 3.2 Run strict OpenSpec validation for this change.
