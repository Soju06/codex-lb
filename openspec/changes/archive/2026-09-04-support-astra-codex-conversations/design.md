## Context

Baseline: dd28d7dff94cdd4919067c1986fd9606b9bbc6b9. Astra catalog/pricing additions are a separate change and overlap public PR #2085. This change works independently with models discovered from an existing ChatGPT account. Its scope is the Responses lifecycle and the existing API-key policy applied to new configuration and steering paths.

## Goals / Non-Goals

Goals: support Astra in the existing subscription proxy, preserve pending async tool results, own steering continuations, enforce reasoning policies, and settle every response exactly once. The first delivery includes these protocol features together.

Non-goals: change the selected default model, add Platform credentials, execute tools inside the proxy, migrate production, expand generic model-source behavior, or invent separate Astra quota windows.

## Decisions

- Reuse existing request policy boundaries. Preserve raw client reasoning identity until policy validation; Ultra becomes Max only on the subscription wire.
- Preserve async call identity as explicit metadata while retaining the existing interruption repair for synchronous calls. Track calls across intervening responses until their actual outputs arrive. An emitted call item finishing is distinct from the external tool finishing.
- Model steering as connection-local owned continuation state linked to the originating request, account and API key. Retain accepted steering until applied, failed, or the connection ends. Automatic response.created must not claim an unrelated queued request. Reserve and settle continuation usage through existing request accounting paths; no shared reservation may settle twice.
- Validate subscription controls after source selection, including explicit API-key reasoning enforcement. External model sources retain their model contract and still enforce API-key update policies. Restricted subscription continuations explicitly reset inherited configuration at the final anchor boundary before request preparation. Preserve valid history order. Configuration updates are incompatible with automatic compaction/truncation and the standalone compact endpoint according to current protocol documentation. Explicit compaction_trigger input remains on /responses when updates are present.
- Treat Codex subscription protocol support as separately verifiable from public API documentation. Local mocked route tests establish proxy behavior; live acceptance needs a bounded subscription check and must not be inferred from those tests.

## Risks / Trade-offs

- Automatic continuations change the one-create/one-response assumption: test terminal-before-continuation, pending tool input, multiple queued steers, failures, disconnect, and unrelated queued creates.
- Async outputs can arrive several turns later: retain legitimate call metadata and avoid synthetic cancellation for those calls, with sync negative controls.

## Migration Plan

No schema migration or default model switch. Apply in an isolated branch, run focused regressions plus relevant lint/type/spec checks, then perform review before any separate publication or deployment. Rollback is the previous release; do not replay queued steering after a lost connection.

## Evidence and Open Questions

- https://developers.openai.com/api/docs/guides/steering
- https://developers.openai.com/api/docs/guides/async-tool-calling
- https://developers.openai.com/api/docs/guides/reasoning#change-reasoning-mid-conversation
- https://github.com/Soju06/codex-lb/pull/2085
- Verify actual Astra subscription control acceptance and account visibility before claiming live readiness.

## Verification scope

Verification is limited to deterministic local tests. Local completion covers the proxy contract; live subscription acceptance remains unverified.
