## Context

The native WebSocket terminal handler already extracts `incomplete_details.reason`. All incomplete responses are currently recorded as errors. The shared terminal handler also serves HTTP bridge requests. Existing Cancelled rendering and metric rules provide the desired behavior without adding a status.

## Goals / Non-Goals

Goals: distinguish the exact controlled interruption reason, preserve usage and the wire event, and make retained legacy rows discoverable under Cancelled.

Non-goals: change other incomplete reasons, raw HTTP SSE classification, retry policy, provider behavior, or rewrite historical aggregate buckets.

## Decisions

Classify only `response.incomplete` with reason `interrupted` as cancelled at settlement. Keep success accounting false and preserve the reason. Legacy `error/interrupted` request-log rows map to cancelled at the operator boundary; filters apply the same predicate. Use raw counts for status splits that cannot be answered from old demand rollups because those lack an error-code dimension. Unfiltered counts keep the existing fast path.

## Risks / Trade-offs

Historical aggregate error counts stay unchanged under the existing historical-bucket contract, while retained row labels are corrected. New cancelled rows flow into all existing non-error metrics. No migration or production rewrite is necessary.

## Example

When a subagent message interrupts a native generation, the client still receives `response.incomplete` and its usage. Request Logs shows Cancelled with reason `interrupted`; token and estimated cost data remain available.
