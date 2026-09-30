# Usage Refresh Policy Context

## Purpose

This context explains how codex-lb derives an account's usage and status, and
how to diagnose disagreements between codex-lb and Codex Desktop or the Codex
CLI quota pill.

codex-lb treats `/wham/usage` as the source of truth for account usage. Other
OpenAI account surfaces can display reset state earlier than `/wham/usage`,
especially during team reset windows, so the dashboard can temporarily show an
account as `rate_limited` even when Codex Desktop says the quota has reset.

## Access and refresh eligibility

A permanent refresh-token failure marks an account `reauth_required` and stops
proactive exchange of that refresh material, while ordinary requests may keep
using the stored access token until its known expiry. Claimless forced refresh
first reads current state: it adopts genuine peer rotation, uses fresh ciphertext
as the guard for unchanged non-terminal material, and fails closed without
exchange when terminal material is unchanged. Before access-token expiry, a
request that reaches this terminal failure may fail over after excluding the
account locally; it does not globally de-route it or move owner-bound continuity
to another account. At known expiry, selection and bridge reuse reject the
account before upstream I/O.

## Upstream Usage Source

codex-lb refreshes account usage by calling:

```http
GET https://chatgpt.com/backend-api/wham/usage
```

The call is made per account on the configured refresh tick, which defaults to
60 seconds. The client lives in
[`app/core/clients/usage.py`](../../../app/core/clients/usage.py), and the
scheduler lives in
[`app/core/usage/refresh_scheduler.py`](../../../app/core/usage/refresh_scheduler.py).

## Pro Max / Pro 500

The upstream identifier is `promax`; the dashboard displays Pro 500. The
account retains its own catalog, including Astra Ultrafast when explicitly
advertised. A Pro-family fallback does not override account-specific model
or service-tier exclusions.

On 2026-09-30, authenticated usage returned a weekly window in the primary
slot, no secondary slot, and percentages rather than an absolute included
allowance. Purchased credits were a separate field. OpenAI's
[Pro tiers](https://help.openai.com/en/articles/9793128-about-chatgpt-pro-tiers)
and [pricing](https://learn.chatgpt.com/docs/pricing) documentation distinguish
plan allowances from API token prices. Relative plan multipliers can support
estimates against a chosen reference, but do not establish an absolute
included-credit grant. Even Pro 200 can have grandfathered or reduced
allowances under the same identifier. The
[Ultrafast consumption multipliers](https://learn.chatgpt.com/codex/agent-configuration/speed)
describe speed-mode consumption, not plan size.

This initial Pro Max support does not calibrate new capacity estimates or
model transitional Pro allowance cohorts. Pro Max has no fabricated absolute
subscription capacity. Routing uses the Pro reference only as a scheduling
heuristic. Credit
summaries retain their legacy estimated-credit subtotal and disclose the
number of unquantified accounts; their percentage is not a whole-pool
percentage. Scoped client pools return null whole-pool percentages for
affected windows. Fleet credit forecasts are unavailable until coverage
can include those accounts.

For example, Pro at 40% used and Plus at 50% used contribute 57960 estimated
weekly capacity and 34020 remaining credits. Pro Max at 20% used adds one
unquantified account and has its own 80% remaining; it does not add an
invented number of credits. Recorded observations remain unquantified when
optional reset metadata is missing. An absent short window is not an
unquantified short window.

## Status Derivation

The fetched usage is fed through
[`apply_usage_quota`](../../../app/core/usage/quota.py), which derives account
status from `primary_window.used_percent`:

- `secondary_used >= 100`, regardless of `primary_used`: `QUOTA_EXCEEDED`
- `used_percent >= 100` on the primary rate-limit window: `RATE_LIMITED`
- `used_percent < 100`: `ACTIVE`

There is no manual reset step inside codex-lb. Recovery is driven by the next
refresh tick that observes a sub-100 value from `/wham/usage`.

## Why Codex Settings Can Disagree

Codex Desktop's Settings -> Account view and `/wham/usage` are fed by different
OpenAI-side data sources:

- `/wham/usage` exposes the rate limiter's internal counter. It updates lazily,
  typically on the next chargeable request through that account, or when its
  internal window crosses `reset_at`.
- Settings -> Account is fed by a separate account/quota view that often picks
  up team-side reset events earlier.

During a reset window it is normal for Settings -> Account to show the reset
state while `/wham/usage` still returns `used_percent: 100` for a short period
afterwards. codex-lb mirrors `/wham/usage` during that window, so the account
stays `RATE_LIMITED` or `QUOTA_EXCEEDED` until upstream catches up.

## Limit Warm-Up Exhaustion Threshold

Reset-confirmed limit warm-up compares the usage sample from before a refresh
with the sample written after that refresh. The pre-refresh sample must be at
or above the configured exhausted threshold, the post-refresh sample must be
below `100`, and `reset_at` must move forward.

The exhausted threshold defaults to `99.0` because some upstream usage payloads
plateau at 99 percent for windows that are practically exhausted. This avoids
missing reset-confirmed warm-ups for those accounts while keeping the reset
confirmation requirement intact. Operators who want the historical strict
behavior can set the threshold to `100.0`.

## Operational Notes

- Wait first. The next request through that account usually wakes the upstream
  rate limiter; codex-lb auto-recovers on the next refresh tick after the
  upstream payload changes.
- The dashboard Force Probe action fires one minimal `responses.create` against
  the selected account and immediately refreshes its usage. The probe uses a
  one-dot prompt and closes the stream after response headers. It omits
  `max_output_tokens`: the Codex Responses endpoint now rejects that field at
  any value. For example, a probe with `max_output_tokens=16` returned HTTP 400
  with `Unsupported parameter: max_output_tokens`, while the same request
  without the field returned HTTP 200. An accepted 2xx probe
  also contributes to that replica's probing-health recovery streak; non-2xx
  results do not restore routing health. Settlement reloads and normalizes
  weekly/monthly and zero-primary-capacity usage like ordinary routing and is
  discarded when newer replica-local runtime activity arrives during that
  snapshot load. The earlier `16` floor addressed
  [#1895](https://github.com/Soju06/codex-lb/issues/1895) when upstream still
  accepted the field; warmup/compact-404 is a separate path.
- Do not manually flip the codex-lb account state to `ACTIVE` while
  `/wham/usage` still reports the account as fully used. That only masks the
  upstream state and can route traffic back to an account that the upstream
  limiter will reject.

## Verification Example

To confirm that the disagreement is upstream rather than codex-lb's mirror,
call `/wham/usage` directly with the same account token codex-lb is using:

```bash
ACCESS_TOKEN=...
ACCOUNT_ID=...   # chatgpt-account-id UUID, not codex-lb's id

curl -s https://chatgpt.com/backend-api/wham/usage \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "chatgpt-account-id: ${ACCOUNT_ID}" \
  -H "Accept: application/json" | jq '.rate_limit'
```

If `primary_window.used_percent` is still `100` here while Settings -> Account
shows the account as reset, codex-lb has nothing fresher to mirror. The account
is inside the upstream propagation window, and the practical fix is to wait or
use the Force Probe action. Check its `probe_status_code`: a non-2xx response
does not count as evidence that the account is healthy.

## Related Work

- [#676 - initial bug report on `/wham/usage` vs. Settings UI divergence](https://github.com/Soju06/codex-lb/issues/676)
- [#677 - dashboard per-account force-probe action](https://github.com/Soju06/codex-lb/issues/677)
