# Review context

Source: https://github.com/Soju06/codex-lb/pull/2444#issuecomment-5866465282

The original archived timing proposal describes candidate behavior, not accepted
maintainer policy. Before readiness, obtain decisions on: 100 ms/two events; the
success/normal-only TTFT and queue cohorts; nullable report medians; custom-source
rows without gateway chunk evidence; and done-only/terminal-output TTFT, including
its routing cohort effect. This repair does not settle those decisions.

## Shared HTTP timing state with #2112

Inspected #2112 at 13096d64cd5710c2575e1c1547f421bacc6ec66f. Its
`_HTTPPhaseLatencies` stores `first_token_ms`, `first_upstream_event_ms`, and
`response_created_ms`; `parse_event` excludes local/synthetic events and
`log_fields` writes the existing persistence funnel. This PR's `_StreamResponseTiming`
stores the attempt anchor, first-token and first-output times, output-event count,
and pending reasoning deltas. These must become ONE state object before the second
PR lands, not two independent TTFT writers.

Preferred landing order: #2111, then #2112, then #2444. Once #2112 lands, extend
`_HTTPPhaseLatencies` with this PR's output evidence and reasoning state; map
`latency_first_token_ms` to its `first_token_ms`, reuse `parse_event` and `log_fields`,
and remove `_StreamResponseTiming`. If maintainers choose #2444 first, #2112 must
extend this PR's carrier and remove its duplicate first-token member. This is a
coordination plan awaiting agreement, not a claim that the unmerged integration
has passed. Integration tests must cover provenance, valid zero vs null, reasoning
first, canonical parsing counts, retries, and a delayed admission/settlement clock.

Example: TTFT at 125 ms from reasoning, first text at 250 ms, and terminal at 1000 ms
use one post-admission attempt anchor; canonical text events after the first are
counted without decoding each JSON payload. Two seconds of local cleanup cannot
change those observations.

## Independent changes

Model-source telemetry is tracked in https://github.com/Soju06/codex-lb/pull/2521.
The unchanged stream entrypoint relocation is tracked in
https://github.com/Soju06/codex-lb/pull/2520. Neither patch remains in #2444's diff.
Their successful independent validation does not approve the disputed metric policy.
