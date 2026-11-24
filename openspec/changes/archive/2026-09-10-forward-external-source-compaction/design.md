## Context

The explicit compact route is currently subscription-only. A prior loopback diagnostic registered a Responses source through the public API, then received `503 no_accounts` from both compact paths while the source received no traffic. This is an accepted feature gap in the single-provider bridge, not evidence that the current subscription-only contract is implemented incorrectly. Refresh the route proof against the pinned base before changing code.

## Goals / Non-Goals

Support explicit compact requests for configured Responses sources with the same source scope, native precedence, admission and usage behavior as ordinary Responses. Preserve complete source history and opaque compact output.

Provider translation, history storage, catalog discovery and implicit terminal-trigger compaction are separate work. This change does not claim actual CPA/provider compatibility or introduce an unmerged PR dependency.

## Decisions

Resolve sources before subscription admission. Preserve native file pins and resolve subscription previous-response and turn-state owners before source dispatch. If an owner requires subscription routing, let the existing compact service perform its complete reconciliation and cleanup. Source resolution failures remain errors.

Use the existing source dispatch owner for concurrency claims, API-key reservations, disconnect detection, settlement and logging. Extend the source HTTP transport with a separate compact entry point that shares the existing non-stream request implementation. Do not duplicate settlement or retry code.

Forward external compact payloads without the subscription-specific history trimming or tool removal. Retain API-key enforcement and strip LB telemetry. Do not infer that Responses support proves compact support; an unsupported source endpoint returns its explicit upstream error, without fallback or another model.

## Risks / Trade-offs

Native continuity can overlap a source model. Public route tests must prove those owners never dispatch externally. The native compact service remains unchanged.

The two public compact contracts differ: SDK output retains the source envelope, while the Codex path uses the existing compact result normalization. Preserve opaque content rather than translating it.

Source timeouts and cancellation must release admission and reservation ownership. Reuse the source dispatcher and verify recovery through subsequent public requests.

No migration or configuration change is needed. Rolling back removes external compact support while preserving source records. Real provider authorization and deployed-host acceptance remain external decisions.
