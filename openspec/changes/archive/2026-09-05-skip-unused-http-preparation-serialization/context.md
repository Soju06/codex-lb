# skip-unused-http-preparation-serialization context

Purpose and implementation boundary are in the [proposal](proposal.md) and [design](design.md); the delta is the normative contract. This is one independently reviewable part of issue #2029, based on pinned main `aec4d7b7f66128ece52c09398c546fddea260d94`.

The controlled investigation used the real application with temporary SQLite, two synthetic Pro accounts and a local scripted upstream. It measured local transport/serialization/persistence work, not real model inference or WAN delays. Healthy retained WebSocket calls stayed on one upstream connection. Historical multi-second/minute waits remain unattributed; no blanket speedup is promised.

A concrete acceptance example is the regression in task1.1: replace only the external nondeterministic boundary or observe the owning seam, then exercise unchanged production behavior. Do not substitute a fake selection, persistence or rendering path for the layer named by the test. No additional configuration, operator migration or live rollout is part of this change.

Related independent changes are `prevent-ssl-starvation-false-reclaim`, `reuse-direct-wss-system-trust`, `publish-http-response-owner`, `observe-http-upstream-latency`, and `skip-unused-http-preparation-serialization`. The architectural narrative is owned by `proxy-runtime-observability/context.md` in the timing scope. A later local integration build combines accepted heads and produces one wheel; it is not another feature scope.

## Accepted implementation evidence

The focused implementation is `05a0c2d661e9d2e20f0bf28f0e02c187e0ed2a50`. Its real-origin regression observes preparation encodes of 0 with inactive consumers, 1 with raw tracing, and 1 for auto-mode sizing; the accepted baseline performs 2 in each case. Removing fallback string refresh fails the existing trace/body equality check. The focused suite passed 1,422 tests plus lint and type checking.

Phase-five acceptance passed 1,870 combined tests with the actual metrics runtime. The matched immutable integration source `ffef2a6f8fcf863e1e6af039efd2fb0e11bbcd60` was compared with main `5ad638b6a4c9c094bcc8866b1d7487173fe3b54e` through the unchanged product and a local origin. The preparation CPU check preserved exact 1,062,374-byte and 8,538,142-byte upstream bodies and reduced owning preparation encodes from 2 to 0. Their median preparation thread CPU was 5.599 to 2.467 ms and 47.415 to 18.947 ms. This bounded result excludes HTTP wire serialization and network/model time.

The combined actual Codex CLI 0.153.4 witness passed normal prewarm/generation/tool continuation and the first-handshake-426 recovery via WebSocket retry. It is protocol evidence, not an HTTP fallback or latency parity claim. Final wheel creation and isolated built-product launch are separate delivery gates performed after this documentation cutover.

Stable behavior and rationale are recorded in the [owning capability context](../../../specs/outbound-http-clients/context.md#responses-http-preparation).
