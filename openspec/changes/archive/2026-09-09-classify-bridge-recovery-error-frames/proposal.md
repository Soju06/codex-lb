# Classify Bridge Recovery Error Frames

## Why

The HTTP responses session bridge can wedge a session permanently (issue #1830). After one genuine mid-turn interruption the bridge rebinds to its stored durable anchor and re-injects it on every attempt. When upstream rejects that anchor with a classifiable previous-response error, two gaps keep the session unrecoverable:

1. The bridge-local recovery gate reads raw error codes without the normalization the WebSocket path gained in the `classify-invalid-previous-response-id` change (#1818): a frame that carries its classifiable code only in `type`, or the terse parameterless ``Invalid `previous_response_id`.`` shape, falls through to the ambiguous-transport class instead of previous-response recovery.
2. Anchor poisoning only counts `stream_idle_timeout` failures, and only on the reader path when admission waiters exist. The wedge observed in production fails eventlessly with `stream_incomplete` (the bridge's masked form of an upstream previous-response rejection), so the retry circuit opens and cools down forever while `http_responses_session_bridge_anchor_poison_failure_threshold` never fires. Operators had to wipe the `http_bridge_*` tables to free sessions.

## What Changes

- Route the bridge-local previous-response recovery gate through the same error-code normalization as the WebSocket rewrite path (code falls back to `type`; the terse parameterless invalid-previous-response shape classifies as a continuity miss).
- Count both ambiguous eventless transport classes — `stream_incomplete` and `stream_idle_timeout` (with its aliased diagnostics) — toward anchor poison, so consecutive same-anchor failures self-heal even when the frame is genuinely unclassifiable. `clean_close` still never poisons.
- Evaluate anchor poison at the shared retirement boundary as well, so a wedged anchored session that fails without admission waiters also clears its poisoned durable anchor once the threshold is reached.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Normalize error frames at the bridge-local recovery gate and widen anchor poisoning to all consecutive eventless same-anchor failures, including the waiterless retirement path.

## Impact

- HTTP bridge recovery gate (`app/modules/proxy/_service/http_bridge/helpers.py`), anchor-poison accounting (`app/modules/proxy/_service/http_bridge/upstream_events.py`, `app/modules/proxy/_service/http_bridge/request_submit.py`, `app/modules/proxy/_service/http_bridge/retry_circuit.py`).
- No API, schema, migration, dependency, configuration, or dashboard changes; the existing poison threshold setting and its default of seven are unchanged.
