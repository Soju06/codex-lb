# Change: Retry upstream model-capacity errors

## Why

Codex can receive upstream failures whose user-facing message is exactly `Selected model is at capacity. Please try a different model.` These failures are temporary provider capacity, not a bad request from the user. When they are classified as non-retryable, Codex CLI surfaces the terminal error and then wastes a reconnect cycle on each affected thread.

## What changes

- Treat upstream model-capacity messages as retryable transient failures even when the upstream code/status looks like `invalid_request_error` / HTTP 400.
- Apply the same classification to pre-dispatch HTTP errors and websocket pre-created replay decisions.
- Surface serialized streaming terminal events without replay because receiving an event does not prove the POST was not accepted.
- Keep post-connect transport/body-read disconnects non-replayed unless typed transport provenance proves the request was not dispatched.
- Keep quota/rate-limit codes classified as quota/rate-limit before message-based transient detection.

## Impact

- Reduces user-visible thread stalls caused by temporary model capacity.
- Preserves the Responses compatibility no-replay boundary for uncertain post-dispatch failures.
- Preserves existing account quota and rate-limit handling.
