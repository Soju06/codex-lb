# Change: Expand TTFT output event coverage

## Why

Request logs currently record time to first token only for text and refusal deltas. Codex requests that first produce reasoning summaries or tool-call arguments therefore have output tokens and total latency but no TTFT or TPS.

## What Changes

- Treat streamed reasoning-summary and tool-call argument deltas, plus non-delta tool-call item-start events, as first-token events.
- Preserve the existing distinction between control events such as `response.created` and token-bearing output.
- Calculate dashboard and report TPS from non-reasoning output tokens generated after TTFT.

## Impact

- The request-log API adds a nullable `reasoningTokens` field for accurate TPS calculation.
- New request logs gain TTFT and TPS coverage for non-text output turns.
- Response routing and streamed bytes remain unchanged.
