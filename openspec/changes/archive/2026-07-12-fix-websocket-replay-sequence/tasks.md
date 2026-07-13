## 1. Sequence exposure tracking

- [x] 1.1 Record finite integer sequence numbers only after direct WebSocket frames are successfully sent downstream.
- [x] 1.2 Reject transparent replay when a request has an exposed numeric sequence watermark.
- [x] 1.3 Apply the sequence watermark guard to close-, quota-, authentication-, and security-work-triggered replay entry points.

## 2. Retryable close and cleanup

- [x] 2.1 Finalize sequenced replay refusals without emitting a second response generation or synthetic terminal frame.
- [x] 2.2 Close the downstream WebSocket with code 1011 after cleanup so compatible clients own the retry.

## 3. Regression coverage

- [x] 3.1 Add a direct `/backend-api/codex/responses` regression for sequence 5 followed by upstream close and a would-be replay sequence 1.
- [x] 3.2 Preserve existing sequence-free one-shot replay behavior and verify cleanup/logging occurs exactly once.
- [x] 3.3 Cover anonymous numeric preambles and event-triggered replay refusal in focused processor tests.

## 4. Validation

- [x] 4.1 Run focused direct-WebSocket tests, changed-file lint, and diagnostics.
- [x] 4.2 Run the affected WebSocket integration suite and strict OpenSpec validation.
