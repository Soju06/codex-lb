# Tasks: bound-http-bridge-gate-waits

- [x] Keep queued HTTP bridge submissions waiting on the per-session
  response-create gate while normal in-flight work completes.
- [x] Keep that wait bounded by `proxy_admission_wait_timeout_seconds`.
- [x] Release queue/account/gate admission state when queued submissions are
  cancelled or fail before being enqueued upstream.
- [x] Add/adjust regression coverage for gate timeout logging and cancellation
  cleanup.
