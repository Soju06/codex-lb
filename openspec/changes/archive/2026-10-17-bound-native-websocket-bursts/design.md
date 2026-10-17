## Context

The shared helper reader already has bounded per-request event queues. Each native WebSocket pump drains into a second queue that currently admits only 64 messages. Waiting in that pump would also block send acknowledgements.

## Goals / Non-Goals

Allow ordinary bursts while retaining finite memory and prompt control processing. Do not add blocking producers, settings, retries, Rust protocol changes or idle-loss behavior.

## Decisions

Generalize the existing byte-counted queue and use it for decoded WebSocket messages. Charge UTF-8 text, decoded binary and the second interpreted payload representation. The WebSocket queue enforces its byte budget even for its first message; retain the HTTP queue's existing single-event allowance.

Use 4096 events and 32 MiB queued payload bytes, matching the existing helper event queue. Keep insertion nonblocking, so a saturated data consumer cannot prevent send acknowledgements. Overflow retains the existing connection-local abort and cleanup, with an account-neutral transport classification.

## Risks / Trade-offs

A bounded queue can still abort a truly stalled consumer. Byte accounting measures retained payload, not complete Python object overhead; the event cap bounds object count. Regression tests cover ordered bursts, byte and event overflow, acknowledgement progress and peer isolation.
