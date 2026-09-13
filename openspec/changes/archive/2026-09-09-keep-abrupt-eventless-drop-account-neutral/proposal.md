# Why

An abrupt upstream websocket drop with no close frame and zero response
events is charged to the account: the HTTP bridge reader only consults
`_classify_upstream_close` when a close frame arrived, so a frame-less
transport reset always sets `penalize_account=True` while a graceful 1000
close before any response event is exempted. That is inverted with respect
to the available evidence — the account never spoke at the application
layer. Three such drops cross the error-backoff threshold and 502 every
continuity-bound follow-up (`previous_response_owner_unavailable`) for 30
seconds while healthy pool siblings idle (issue #1754).

# What Changes

- Classify an abrupt upstream websocket ending — a terminal close or receive
  error with no upstream-authored close frame (the synthetic abnormal-closure
  code 1006 counts as frame-less), no established account-neutral transport
  classification, and no observed application-layer output — as
  account-neutral in the HTTP bridge reader failure path: no `record_error`
  health write for the individual drop.
- Keep the existing penalty when an upstream-authored close frame arrived
  (including non-clean codes such as 1008/1011), when application-layer
  output was already observed (streamed response events or a buffered
  reasoning prelude), or when a non-terminal protocol-invalid frame (for
  example a binary message) triggered the failure, and keep all established
  account-neutral transport codes on their existing contract.
- Feed account-neutral eventless drops that settle their pending requests as
  failures into the existing windowed eventless account drain signal so
  repeated drops on the same account still drain it (same threshold/window
  as repeated eventless upstream timeouts), keeping genuine account faults
  visible. Drops recovered by the bounded pre-created replay keep their
  existing behavior.
- The per-bridge retry circuit continues to record the failure at bridge
  scope, unchanged.

# Capabilities

## Modified Capabilities

- `responses-api-compat`: HTTP bridge abrupt eventless upstream drops must
  stay account-neutral for per-drop health writes while repeated drops still
  drain the account through the windowed eventless failure signal.

# Impact

Continuity-bound conversations survive sporadic infrastructure resets
instead of 502-storming against a self-inflicted 30-second owner backoff.
Accounts whose sockets repeatedly drop eventlessly are still drained by the
existing windowed signal. Clean-close, close-frame, and mid-stream drop
semantics are unchanged, as is the per-bridge retry circuit.
