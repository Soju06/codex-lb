## 1. Implementation

- [x] 1.1 Reproduce fresh-client 409 and add a shared direct-source metadata classification projection without changing subscription replay or forwarding.
- [x] 1.2 Cover fresh messages, supported tool/agent envelopes, malformed metadata and ownership boundaries on real routes and across replicas.

## 2. Verification

- [x] 2.1 Run mapped regressions, lint, formatting, type checks and strict OpenSpec validation.
- [x] 2.2 Complete independent review, fix findings, sync stable specs/context and archive verified artifacts.
- [x] 2.3 Commit and push the scoped fix, deploy through HA surge, and verify pooled synthetic continuity on all backends.
