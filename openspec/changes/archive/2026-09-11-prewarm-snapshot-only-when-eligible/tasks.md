## 1. Regression coverage

- [x] 1.1 Prove a payload with no warm-up to send resolves no snapshot, reads
  no settings and opens no database session, while still recording
  `prewarm_status=skipped` under the lock with the session marked prewarmed.
- [x] 1.2 Prove the eligible path is unchanged: one pre-lock read, none while
  the lock is held.
- [x] 1.3 Prove the prewarm timeout path hands the admission gate and the
  reconnect that exact snapshot object, and that it is the only settings read.

## 2. Implementation

- [x] 2.1 Build the warm-up before the lock and resolve the snapshot only when
  one will be sent.
- [x] 2.2 Keep the locked body's `session.prewarmed` bookkeeping and skip
  statuses byte-for-byte identical.

## 3. Verification

- [x] 3.1 Run the HTTP-bridge and proxy-utils unit suites and the bridge
  integration suite.
- [x] 3.2 Run Ruff, Ty, strict OpenSpec validation, and an archive simulation
  that archives `no-settings-reads-under-prewarm-lock` first.
