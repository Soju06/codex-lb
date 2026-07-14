## 1. Direct WebSocket handoff

- [x] 1.1 Preserve one downstream receive owner across receive polling, idle
  checks, and clean upstream-generation retirement.
- [x] 1.2 When upstream-reader and downstream-receive awaitables complete
  together, abort before replay if the receive failed, was cancelled, or
  yielded `websocket.disconnect`; only a successful `websocket.receive` is
  retained while upstream retirement and any earlier replay run first.
- [x] 1.3 Detach and await the retained downstream receive task on every normal,
  cancellation, and exception exit path, containing any secondary receiver
  failure so primary errors and resource cleanup are preserved.
- [x] 1.4 Re-harvest an upstream replay that becomes available during request
  preparation, owner lookup, or response-create admission, retaining one
  prepared newer request until the replay completes.
- [x] 1.5 Recheck retained downstream terminal outcomes during upstream
  retirement and at replay connect, capacity-acquisition, and send boundaries;
  settle resources acquired by an already in-flight operation before exit.

## 2. Verification

- [x] 2.1 Cover clean-close handoff, simultaneous and asynchronously late
  replay ordering, failed-receiver and disconnect replay suppression at both
  wait sites, retirement and reconnect boundaries, and replay-connect cleanup
  with focused deterministic regressions.
- [x] 2.2 Run the focused direct Responses WebSocket integration tests.
- [x] 2.3 Run strict change validation and repository spec validation.
