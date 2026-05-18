## Why

The compact workaround that buffered `/codex/responses/compact` through `/codex/responses` fixed one class of `502` failures, but it crossed a contract boundary: a compact request stopped returning the provider-owned compaction window and started returning a synthesized standard Responses payload.

That tradeoff improved transport resilience while silently breaking continuation semantics. Downstream clients lost the canonical compacted window they were supposed to feed into the next `/responses` call, so the agent could appear to "forget" prior context after compaction.

This needs a dedicated architectural correction, not another transport-only patch. Compact and normal Responses are different upstream contracts and must not share a surrogate execution path by default.

## What Changes

- define a dedicated compact transport boundary separate from normal Responses session transport
- require successful compact responses to remain opaque, canonical next context windows returned without proxy-side rewriting or surrogate reconstruction
- require compact failures to fail closed without silently substituting `/codex/responses` or reconstructing compact output from SSE events
- allow only bounded same-contract retries against `/codex/responses/compact` for provably safe transport failures and `401 -> refresh -> retry`
- add contract-focused tests and tracing so future transport hardening cannot regress compact semantics unnoticed

## Impact

- Code: `app/core/clients/proxy.py`, `app/core/openai/models.py`, `app/core/openai/parsing.py`, `app/modules/proxy/service.py`
- Tests: `tests/unit/test_proxy_utils.py`, `tests/integration/test_proxy_compact.py`, compact continuation coverage near `tests/integration/test_openai_compat_features.py`
- Specs: `openspec/specs/responses-api-compat/spec.md`, `openspec/specs/responses-api-compat/context.md`
