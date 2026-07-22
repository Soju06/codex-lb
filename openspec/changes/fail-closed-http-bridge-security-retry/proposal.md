## Why

HTTP bridge security-work failover must not reconnect a request once the client
has an upstream continuity anchor or model output, including deferred reasoning.

## What Changes

- Make HTTP bridge security retry fail closed after `response.created` or any
  upstream model output.
- Preserve a bounded, file-free pre-created retry while clearing stale affinity
  and restoring state after reconnect failure.

## Impact

- HTTP bridge security retry and Responses compatibility contract.
