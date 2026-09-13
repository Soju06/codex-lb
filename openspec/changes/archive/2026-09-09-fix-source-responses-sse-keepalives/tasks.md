## 1. Regression coverage

- [x] 1.1 Add integration regressions for a pre-first-event keepalive on source-routed `/v1/responses` and for reservation settlement when normalization ends on an upstream error frame.
- [x] 1.2 Add reassembly regressions for split chunks, split UTF-8, CR/LF/CRLF and mixed blank-line separators, preserved terminator bytes, and the bounded event-size failure.
- [x] 1.3 Add a regression proving unparseable source data blocks pass through verbatim without a synthesized terminal event.
- [x] 1.4 Add a perf regression guard proving the shared SSE separator scan stays find-based instead of per-byte.

## 2. Source stream adaptation

- [x] 2.1 Reassemble source byte chunks into complete SSE event blocks with an incremental UTF-8 decoder and preserved terminators.
- [x] 2.2 Apply public Responses normalization and SSE comment keepalives on the public route; preserve `codex.*` events and `codex.keepalive` heartbeat framing on the native Codex route.
- [x] 2.3 Keep reservation settlement outermost so normalization early-return still settles, and close iterator and owning stream deterministically on completion, failure, or cancellation.
- [x] 2.4 Locate SSE separator candidates with C-level `bytes.find`, classifying line endings only at candidate positions.

## 3. Verification

- [x] 3.1 Run the focused source-responses integration subset, the SSE separator unit subset, and lint on changed files.
- [x] 3.2 Validate the scoped OpenSpec change strictly and keep the delta in sync with the implementation.
