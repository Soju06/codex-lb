# Subagent Prompt-Cache TTL - Empirical Findings

## Session Origin

OpenCode sends `x-parent-session-id` on requests originating from subagents. Tool calls are sent as ordinary conversation requests and are not used as a lifecycle signal.

## Fork Behaviour

When multiple requests share one OpenCode session header but carry no explicit turn-state or `previous_response_id`, codex-lb may create an **unanchored parallel fork**. This feature does not classify those requests as subagents.

## Timing Profile (measured on 2026-07-13)

| Stage | Duration |
|---|---|
| Actual upstream work (single read) | <60s |
| Bridge session idle retention (fork) | 3600s (1h) |
| Stream lease stale reclaim (safety net) | ~7260s (2h) |

Tool-call concurrency is outside this change because tool calls are represented as ordinary conversation requests.

## Capacity Impact

Subagent bridge sessions are closed at response-stream completion. An optional positive subagent mapping TTL affects only sticky mapping retention, not stream-lease lifetime.

## Visibility Gap

Active bridge-session visibility is outside this change.
