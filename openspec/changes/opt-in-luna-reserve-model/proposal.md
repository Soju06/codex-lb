## Why

Eligible Plus and Pro accounts carry a Luna Reserve allowance: a quota bucket
upstream reports as `metered_feature: base_model_inference` with
`limit_name: gpt-reserve`, separate from the 5h and 7d chat windows. codex-lb
does not route to it, so the allowance sits unused while the account's ordinary
quota is what runs out. Issue #2413.

The obvious shape is an automatic fallback: notice the account is exhausted,
switch that turn to the reserve, switch the reported model back on the way out.
That shape was prototyped and rejected on the issue. It reroutes a session the
operator did not ask to reroute, which busts the prompt cache and silently
changes which model answered — a Max-tier task quietly finishing on Luna. It
also fails the first simplicity gate: a fallback that engages on its own is a
behavior change on the base install path.

Selecting the reserve is a choice about which allowance to spend. The operator
is the one who knows whether this turn is worth it, so this change makes
`gpt-reserve` an ordinary model they select, gated by a policy that ships off.

## What Changes

- Register the reserve bucket in `config/additional_quota_registry.json` under
  `quota_key: base_model_inference`, mapping model `gpt-reserve` and matching
  the upstream `limit_name` and `metered_feature`. This reuses the mechanism
  `gpt-5.3-codex-spark` already uses: an account whose standard quota is spent
  stays selectable for a request gated by that bucket, and ranking reads the
  bucket's own window.
- Add `disabled` to the additional-quota routing policies. Unlike the ranking
  policies, it removes the bucket from routing entirely. The reserve ships
  `disabled`, so nothing changes for an operator who does not go looking.
- Surface the policy in the dashboard's existing additional-quota routing
  control. It rides the `additional_quota_routing_policies` setting that is
  already persisted and already rendered, so this adds no new setting, no new
  `CODEX_LB_*` variable and no migration.
- Publish `gpt-reserve` in the model registry so clients can pick it, with
  pricing and alias entries.
- Send `supportsLunaReserve=true` on the usage fetch so upstream includes the
  reserve bucket in `additional_rate_limits`.
- Refuse a request for `gpt-reserve` while the policy is `disabled` with
  `additional_quota_routing_disabled` and a message naming the dashboard
  control, ahead of the reauthentication rescue so a switched-off bucket cannot
  be routed by a fallback path.

Explicitly not included: quota watching, reserve-mode account state, wire-level
model substitution, and rewriting the model name in downstream events. A
request for `gpt-reserve` is dispatched as `gpt-reserve` and reported as
`gpt-reserve`.

## Capabilities

### New Capabilities

None. This extends account-routing and the additional-quota mechanism that
already carries `codex_spark`.

### Modified Capabilities

- `account-routing`: additional-quota routing policies gain `disabled`, and a
  bucket set to `disabled` is refused at selection with a distinct error code.

## Impact

- `config/additional_quota_registry.json` — reserve bucket definition.
- `app/modules/usage/additional_quota_keys.py` — `disabled` joins the canonical
  policy set; unlisted values still normalize to `inherit`.
- `app/modules/proxy/load_balancer.py` — the selection gate, the refusal code,
  and the ranking-override path that must never emit `disabled`.
- `app/modules/proxy/api.py`, `app/modules/proxy/service.py` — the new code
  joins the selection-unavailable and local-refusal registries.
- `app/core/openai/model_registry.py`, `app/core/usage/pricing.py`,
  `app/modules/proxy/request_policy.py` — `gpt-reserve` as a listed model.
- `app/core/clients/usage.py` — the `supportsLunaReserve` query parameter.
- `frontend/src/features/settings/` and the three locale files — the `Off`
  option on the existing control.

## Open Questions

- Whether upstream returns the reserve bucket without `supportsLunaReserve=true`
  is unverified. The parameter is sent either way; an upstream that does not
  know it ignores it.
- What upstream returns when `gpt-reserve` is dispatched on an account with no
  reserve eligibility has not been observed. An unrecognized refusal already
  classifies as `non_retryable` and surfaces rather than failing over across the
  pool, which is the wanted behavior; a refusal shaped as a 429 would instead be
  classified as quota and retried across accounts. Worth revisiting once a real
  response exists.
