## 1. Eventless-close recovery

- [x] 1.1 Invoke complete transcript recovery only from zero-event transport
  close/timeout handling when explicitly allowed by the complete-transcript
  setting.
- [x] 1.2 Reuse durable operation rebind, circuit-generation, account-pinning,
  and one-replay guards; retain fail-closed fallback.

## 2. Replay compatibility

- [x] 2.1 Strip an exact echoed response subsequence when a client prefixes it
  with fresh input.
- [x] 2.2 Remove only byte-equivalent duplicate tool call/output echoes and
  reject conflicting duplicate IDs.

## 3. Regression coverage

- [x] 3.1 Cover prefixed tool-output echoes and exact duplicate tool pairs.
- [x] 3.2 Cover the HTTP bridge transport-close recovery call path.
- [x] 3.3 Run strict OpenSpec validation before publishing the change.
