## 1. Protocol

- [x] 1.1 Validate and admit `response.steer` on an owned Astra WebSocket
- [x] 1.2 Bind automatic successors and explicit continuations to the parent
- [x] 1.3 Prepare explicit continuation before releasing the placeholder

## 2. Accounting

- [x] 2.1 Extend one successor reservation for additional queued steers
- [x] 2.2 Reduce only the unapplied increment on rejection; FOR UPDATE on that path

## 3. Verification

- [x] 3.1 Unit steering scenarios including late successor and prepare-before-swap
- [x] 3.2 Strict OpenSpec validation of this change
- [x] 3.3 Contain placeholder refund failures after explicit registration
- [x] 3.4 Consume anonymous terminals from suppressed late successors
- [x] 3.5 Infer apply_patch_call output for explicit continuations
- [x] 3.6 Sanitize upstream response.steer.failed before forwarding
- [x] 3.7 Revalidate and atomically swap explicit continuations after preparation, owner resolution and admission; prove reader rejection/assignment races and cleanup on the WebSocket surface
- [x] 3.8 Retain failed replaced-placeholder releases for bounded socket cleanup; prove transient recovery, accounting, unrelated progress, cancellation and owned teardown with deterministic WebSocket fault injection
