## Why

HTTP bridge security-work failover must not reconnect a request once the client
has an upstream continuity anchor or model output, including deferred reasoning.

## What Changes

- Make HTTP bridge security retry fail closed after `response.created` or any
  upstream model output.
- Preserve a bounded, file-free pre-created retry while clearing stale affinity
  and restoring state after reconnect failure.
- Preserve hard session-header ownership during pre-created reconnects, clear
  stale turn-state headers, and retire partially rebound replacements when the
  resend fails.

## Impact

- HTTP bridge security retry and Responses compatibility contract.
