## 1. One reading of the sentence

- [x] 1.1 Add `is_upstream_usage_limit_message` to `app/core/errors.py`, matching after folding each run of non-alphanumeric characters to a single space.
- [x] 1.2 Add `is_upstream_usage_limit_rejection` to `app/modules/proxy/helpers.py`, gated to the two normalized codes that carry no classification decision, and status-independent apart from the `invalid_request_error` guard.

## 2. Classification

- [x] 2.1 Classify a message-derived usage limit `rate_limit` in `classify_upstream_failure`, ahead of the transient branch and behind the code branches.

## 3. Gates

- [x] 3.1 Put the pre-visible streaming retry gate (`_should_retry_stream_error`) on the predicate.
- [x] 3.2 Put `_should_penalize_stream_error` on the predicate and pass the failure's message at every call site that has one, on both transports.
- [x] 3.3 Answer the WebSocket transparent-replay code from the predicate, under the `usage_limit_reached` code, so an owner-pinned turn and its health write take the coded form's path.
- [x] 3.4 Trigger the immediate coalesced usage refresh from the rejection instead of the literal error code.

## 4. Coverage

- [x] 4.1 Drive the real `_stream_once` from a unit harness that puts the frame on the wire, covering the pre-visible walk, the post-visible health write, and the unrelated code-less frame that must stay terminal.
- [x] 4.2 Cover the product paths: the HTTP walk, the last account of a walk, the post-lifecycle frame, and the WebSocket bench-and-replay, each against the coded control.
- [x] 4.3 Cover the predicate's delivery forms and its refusal to reverse a code that already decided.
- [x] 4.4 Pass lint, format, the touched suites, and strict OpenSpec validation.
