## 1. Accepted-response capacity replay

- [x] 1.1 Classify an accepted, output-free terminal capacity event on the
      native Codex HTTP bridge as replayable.
- [x] 1.2 Classify an accepted, output-free abrupt upstream close the same way,
      falling through to the pre-created retry circuit when it is refused.
- [x] 1.3 Replay once on the same account after the existing transient backoff,
      bounded by downstream attachment, request deadline, and replay budget.
- [x] 1.4 Preserve the accepted response identity downstream: suppress the
      replay's `response.created` and rewrite later events to the ID the client
      already received.
- [x] 1.5 Restore every mutated identity and error-override field when a replay
      does not reach the wire.

## 2. Direct Responses retry gaps

- [x] 2.1 Retry non-streaming Responses overload envelopes that do not carry an
      HTTP 500.
- [x] 2.2 Retry an initial output-free upstream stream EOF for unanchored
      requests.
- [x] 2.3 Keep the real upstream error when the transient budget is exhausted,
      and reserve budget for the attempt a backoff is paid for.

## 3. Validation

- [x] 3.1 Negative controls: public OpenAI SDK streams, prior model output,
      billed output tokens, other pending requests, exhausted replay budget,
      and a numbered frame already delivered downstream.
- [x] 3.2 Regression coverage that a bridge client which already received
      `response.created` never receives a second one across the replay.
- [x] 3.3 Regression coverage that client-disconnect cleanup is not blocked by
      an in-flight capacity replay.
- [x] 3.4 Run focused pytest, `uv run pytest tests/unit`, `uv run ruff format`,
      `uv run ruff check`, and `uv run ty check`.
