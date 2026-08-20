## Context

The Responses API reports exact reasoning usage at `usage.output_tokens_details.reasoning_tokens`. codex-lb already parses that terminal usage object into `request_logs.reasoning_tokens` for direct Codex subscription traffic over HTTP and WebSocket and exposes it as `reasoningTokens` from the request-log API. The request table and reports page omit the value, while reports aggregate only input, cached-input, and inclusive output totals.

## Goals / Non-Goals

**Goals:**

- Make per-request reasoning usage visible without a database query.
- Aggregate reported reasoning usage over the same date and account/model/user-agent filters as the existing reports totals, with summary coverage for requests whose count is known.
- State the subset relationship in the UI contract so reasoning is never added to output a second time.

**Non-Goals:**

- Estimate reasoning usage from reasoning summaries or visible text.
- Change token limits, pricing, cost calculation, or API-key quota enforcement.
- Backfill responses whose upstream terminal event did not provide usage.
- Extend reasoning-detail capture for custom OpenAI-compatible model sources; their forwarding parser is a separate protocol change.

## Decisions

- Use the upstream-provided count already stored in `request_logs.reasoning_tokens`; no tokenizer or heuristic is introduced.
- Keep `outputTokens` inclusive of reasoning tokens. `reasoningTokens` is an additive breakdown field, not another component of total tokens.
- Add nullable `reasoningTokens` to each reports daily row, plus `totalReasoningTokens` and `reasoningUsageKnownRequests` to the reports summary. An all-unknown day remains null, while a known zero remains zero. The previous-window token comparison remains input plus inclusive output, using the existing reasoning-only fallback when an older row lacks an output total.
- Render the reasoning subset as secondary request-row metadata and as an explicit request-detail field. Reports render it in the token summary, daily table, and CSV.

## Risks / Trade-offs

- [Risk] Operators add reasoning to output and overstate usage. The spec and UI copy identify reasoning as included in output, and total-token calculations continue to use input plus inclusive output without adding reasoning again.
- [Risk] Legacy, cancelled, or interrupted rows may have no terminal reasoning count. Request history leaves the value unknown, an all-unknown daily aggregate remains null, reports label the aggregate as reported reasoning, and summary coverage states how many requests supplied a count. Missing values are excluded rather than inferred as known zero.

## Migration Plan

Ship the additive API and dashboard fields together. Rollback removes the new fields and rendering; the existing `request_logs.reasoning_tokens` data remains intact.

## Open Questions

None.
