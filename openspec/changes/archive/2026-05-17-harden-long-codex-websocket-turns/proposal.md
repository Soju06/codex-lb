## Why

Long-running Codex turns can legitimately spend more than five minutes between
observable upstream stream events while the agent is compacting context or
executing tool-heavy work. The previous defaults treated that silence as a
local timeout: compact budget was capped at 75 seconds and upstream stream idle
watching at 300 seconds. That made the proxy return `upstream_unavailable` or
surface `stream_incomplete` even while the client still expected the same turn
to continue.

The failover path also did not penalize an account when an upstream websocket
closed before the pending streamed response reached a terminal event. The
client saw an incomplete stream, but the next routing decision had no recent
transient-failure signal for that account.

## What Changes

- Raise the default compact request budget to 180 seconds.
- Raise the upstream stream idle timeout to 600 seconds so long Codex turns have
  the same end-to-end budget as the general proxy request budget.
- When an upstream websocket closes with pending streamed responses that have
  not reached a terminal event, record a transient account error before
  completing those pending futures with `stream_incomplete`.

## Impact

- Long Codex turns are less likely to be interrupted by local proxy watchdogs.
- Account selection can temporarily avoid an upstream account that just dropped
  in-flight websocket responses.
- The change does not hide terminal upstream failures. Completed responses,
  explicit upstream error frames, and normal client disconnect handling keep
  their existing behavior.
