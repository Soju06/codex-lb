## Context

`AccountsService._send_probe_request` posts a pinned, load-balancer-bypassing
`responses.create` so operators can wake `/wham/usage`. The body still hardcodes
`max_output_tokens=1`, which matched the original "tiny quota" intent and the
`usage-refresh-policy` scenario. Current Codex rejects values below 16 with
HTTP 400, so every Force Probe lands as `probeStatusCode: 400` and never
starts the window (#1895, author-verified: 1 → 400, 16 → 200).

Limit warm-up uses `max_output_tokens=4` on a different path. That path is
out of scope; do not share a constant or conflate the two.

## Goals / Non-Goals

**Goals:**

- Send a probe body Codex accepts (`max_output_tokens=16`).
- Keep the probe otherwise identical: one request, `stream=true`,
  `store=false`, pinned account, no load-balancer scoring.
- Align the `usage-refresh-policy` probe scenario with that floor.

**Non-Goals:**

- Warm-up / compact-404 (#1895 other half, #1811).
- Making the floor configurable (`CODEX_LB_*`).
- Changing probe timeouts, model default, or response schema.
- Changing limit-warmup's separate token value.

## Decisions

1. **Hardcode 16, not a setting.** The floor is an upstream contract, not an
   operator preference. A new `CODEX_LB_*` knob would ask operators to
   rediscover a value Codex already enforces. Why-not-a-default: it *is* the
   default.

2. **Named module constant next to the other probe knobs.** Keep
   `PROBE_MAX_OUTPUT_TOKENS = 16` beside `DEFAULT_PROBE_MODEL` so the floor
   is one place in code and tests can assert the public body without
   duplicating a magic number in multiple call sites.

3. **Do not reuse limit-warmup's `4`.** That path is a different request and
   still below the verified floor. Sharing it would either leave warmup
   broken or silently change warmup traffic in this PR.

## Risks / Trade-offs

- [Upstream raises the floor again] → Mitigation: one constant plus the
  spec scenario; a later one-line bump is enough. No setting until Codex
  actually varies this per account or model.
- [16 spends more quota than 1] → Mitigation: still a single minimal
  completion; issue author confirmed 16 completes. A rejected probe spends
  nothing useful and leaves windows unstarted.
- [Partial #1895 cover looks like a full close] → Mitigation: PR states
  warmup/compact-404 is out of scope and uses `Related to #1895` so GitHub
  does not auto-close the remaining half.
