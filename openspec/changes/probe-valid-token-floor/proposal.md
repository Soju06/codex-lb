# Why

Dashboard Force Probe sends `max_output_tokens=1` on the pinned
`responses.create` used to wake the upstream limiter. Current Codex
rejects values below 16 with HTTP 400, so every operator probe returns
`probeStatusCode: 400` and never starts the usage window. Issue #1895
verified that 16 is accepted.

# What Changes

- Raise the account-probe `max_output_tokens` from `1` to `16`, the
  verified Codex token floor.
- Update `usage-refresh-policy` so the probe scenario requires that
  floor instead of `1`.
- Keep the request otherwise unchanged (`stream=true`, `store=false`,
  pinned account, bypass load-balancer scoring).

This change does **not** include the warmup / compact-404 half of
issue #1895, limit-warmup's separate `max_output_tokens=4` path, or any new
setting.

# Capabilities

## New Capabilities

- None

## Modified Capabilities

- `usage-refresh-policy`: the operator probe `responses.create` MUST
  use `max_output_tokens=16` (the current Codex token floor), not `1`.

# Impact

- `app/modules/accounts/service.py` (`_send_probe_request`)
- Probe unit coverage that asserts the upstream JSON body
- `openspec/specs/usage-refresh-policy/spec.md` and its context notes
- Dashboard Force Probe behavior: same endpoint and response shape;
  successful probes can now receive a 2xx upstream status instead of
  a guaranteed 400
