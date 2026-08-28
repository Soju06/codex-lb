## Why

Issue #1313 identifies a remaining overload-code gap after the existing
`overloaded_error` retry work from #565. Upstream can emit
`server_is_overloaded` as a terminal Responses event after the HTTP stream has
already returned status 200. Because that code is absent from both the failure
classifier and streaming retry set, codex-lb can surface the event instead of
using its bounded pre-visible retry and failover behavior.

## What Changes

- Classify `server_is_overloaded` as a retryable transient even when no HTTP 5xx
  accompanies the streamed error envelope.
- Allow the streaming retry path to handle `server_is_overloaded` with the same
  bounded retry behavior as other transient server errors.
- Add unit and externally routed integration coverage for the no-5xx SSE path.
- Extend the Responses compatibility requirement to cover both known upstream
  overload codes.

## Impact

- Fresh requests can recover transparently from the newer transient overload
  code instead of stopping an agent mid-task.
- Retry remains bounded by the existing stream retry budget and remains disabled
  once downstream-visible output makes replay unsafe.
- No API, schema, or configuration changes are introduced.
