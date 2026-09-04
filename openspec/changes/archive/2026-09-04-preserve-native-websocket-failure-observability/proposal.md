## Why

Native WebSocket receive failures currently collapse into the public
`stream_incomplete` message before request-log persistence. The adapter knows
whether the failure came from the native transport, protocol, helper, liveness
watchdog, or the bounded application-message queue, but that provenance is
discarded. Operators therefore cannot distinguish an upstream socket reset
from local consumer backpressure when investigating a failed Responses turn.

## What Changes

- Preserve the native WebSocket failure phase and a credential-safe detail on
  the internal upstream message.
- Persist that metadata in existing request-log `failure_phase` and
  `failure_detail` fields for terminal WebSocket request failures.
- Record the bounded application-message queue depth and limit when it
  overflows, and emit one structured warning for native receive failures.
- Keep the downstream error envelope, retry/health policy, queue limits, and
  native helper wire protocol unchanged.

## Non-goals

- Do not increase or remove the bounded application-message queue limit.
- Do not expose native exception text, account credentials, request payloads,
  or response identifiers in the downstream error.
- Do not add a database migration or change retry, failover, or account-health
  decisions.

## Impact

- Native egress error metadata and WebSocket relay finalization.
- Existing request-log rows gain diagnostic values only for newly classified
  native receive failures; historical rows remain unchanged.
- Unit coverage for queue overflow, native phase propagation, and terminal
  request-log metadata.
