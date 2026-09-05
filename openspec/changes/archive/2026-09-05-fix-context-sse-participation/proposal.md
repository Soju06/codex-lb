# Record HTTP context participation only after parsed SSE events

## Why
PR #2102 Codex review found that an SSE comment followed by EOF or a startup error leaves a false history participant. CodeRabbit also identified ambiguous validation counts in the previous review notes.

## What Changes
- Require a parsed upstream event before recording HTTP participation, including when comments precede that event.
- Preserve pre-dispatch API-key ownership and downstream SSE forwarding.
- Clarify that earlier validation counts include overlapping suites and reruns.

## Impact
HTTP streaming context bookkeeping, focused route tests and validation documentation. No new settings or deployment.
