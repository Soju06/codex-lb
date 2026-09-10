## 1. Persist rejection evidence

- [x] 1.1 Add generation and rejected scope schema with reversible migration and historical-row coverage.
- [x] 1.2 Advance generation atomically on every rejection and carry scope through immediate and deferred writers.

## 2. Verified probe recovery

- [x] 2.1 Add bounded completed-stream evidence and singleflight admission to the existing probe.
- [x] 2.2 Recover with pre-dispatch generation and identity CAS, preserving runtime and owner fences.

## 3. Verification

- [x] 3.1 Public route regressions cover restart, successful completion, failed/incomplete streams, concurrent probes and changed account state.
- [x] 3.2 Verify migration graph, formatting, typing and strict OpenSpec validation; record exact disposable database identities.
