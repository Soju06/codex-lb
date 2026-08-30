# Probe valid token floor

## Purpose

Force Probe is the operator action that wakes a lazy `/wham/usage` window
without going through load-balancer selection. It only works if upstream
accepts the probe body.

## Decision

Use `max_output_tokens=16`. Issue #1895's author verified that Codex
returns 400 for `1` and 200 for `16`. That floor is an upstream contract,
so it is a hardcoded constant, not a `CODEX_LB_*` setting.

## Non-goals

- Warm-up / compact-404 (the other half of #1895)
- Limit-warmup's separate `max_output_tokens=4` path
- Configurable probe token budget

## Failure mode

If the probe still sends `1`, every Force Probe returns
`probeStatusCode: 400`, the limiter does not re-evaluate, and 5h windows
cannot be started from the dashboard after a reset.

## Example

```json
{
  "model": "gpt-5.5",
  "instructions": "Respond with a single dot.",
  "input": [{"role": "user", "content": [{"type": "input_text", "text": "."}]}],
  "max_output_tokens": 16,
  "stream": true,
  "store": false
}
```

## Related

- Normative requirement: `openspec/specs/usage-refresh-policy/spec.md`
  ("Operators can probe an account to wake the upstream limiter")
- [#1895](https://github.com/Soju06/codex-lb/issues/1895) — this change
  covers the probe 400 only
