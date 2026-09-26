## 1. Durable ownership

- [x] 1.1 Add scoped ownership storage and migration; verify atomic conflicts, expiry, upgrade/downgrade and schema drift.
- [x] 1.2 Record references before delivery and resolve stateful requests; verify immediate continuation, encrypted output, replica changes and failed persistence through real routes.

## 2. Replay safety

- [x] 2.1 Gate rotation/failover on portable input or known ownership; verify missing/conflicting ownership, key scope and replaced credentials.
- [x] 2.2 Reject Responses redirects without replay; verify JSON/streaming redirect regressions and unchanged other source protocols.

## 3. Verification and documentation

- [x] 3.0 Resolve independent review findings with route coverage for replayed output IDs, early delta publication, JSON cancellation settlement, and concurrent PostgreSQL ownership refresh/retention.
- [x] 3.1 Run pool/dispatch/forwarding and migration regression suites plus scoped lint/type and architecture checks.
- [x] 3.2 Sync user documentation and normative specs, run strict OpenSpec validation, verify and archive the completed change.
