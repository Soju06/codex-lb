# GPT-5.6 bootstrap and reasoning context

## Purpose and scope

The bootstrap catalog keeps Codex model discovery usable before the first live
registry refresh. This change mirrors the contract-relevant GPT-5.6 metadata
published by OpenAI in `codex-rs/models-manager/models.json` at commit
`3380969a29134630d56feb6218e8e8dcc5e8196d`. It does not add speculative model
aliases, pricing, or request-log filtering behavior.

## Decision rationale

`ultra` is a Codex client-facing effort that enables proactive multi-agent
behavior. The Codex client converts it to `max` when constructing the upstream
request. codex-lb therefore preserves `ultra` in catalogs, API-key policies,
automation configuration, and dashboard controls, but uses one wire
canonicalization rule (`ultra` to `max`) at every ChatGPT/Codex forwarding path.

Dropping `ultra` would make the bootstrap catalog diverge from upstream.
Persisting `max` in place of a selected `ultra` would lose operator intent and
break API round trips.

## Constraints and failure modes

- Clients older than `0.144.0` cannot drive the GPT-5.6 code-mode/Responses-Lite
  contract, so all three bootstrap entries retain the upstream minimum version.
- Literal wire `ultra` may be rejected or silently degraded by upstream, so it
  must never leave the proxy as a reasoning effort, including on direct
  OpenAI-compatible model-source routes.
- Responses-Lite requires preservation of `additional_tools`; this change is
  based on main after PR #1161, which supplies that transport behavior.
- Live registry data remains authoritative after refresh.

## Example

An API key may return `enforcedReasoningEffort: "ultra"` from dashboard CRUD.
When that key handles a Responses request, codex-lb forwards
`reasoning: {"effort": "max"}` while request logs and the stored policy continue
to identify the operator's `ultra` selection.
