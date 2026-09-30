# Pro Max contract and evidence

The upstream wire identifier is `promax`; Codex labels it Pro 500. The
official personal-plan comparison describes Pro 100, Pro 200 and Pro 500,
with Astra Ultrafast available only to Pro 500 among these plans.

Sources retrieved 2026-09-30:

- https://help.openai.com/en/articles/9793128-about-chatgpt-pro-tiers
- https://learn.chatgpt.com/docs/pricing
- https://learn.chatgpt.com/codex/agent-configuration/speed
- https://github.com/openai/codex/blob/d42056091aded7feb1d88ac7e83972108b2aa478/codex-rs/protocol/src/auth.rs
- https://github.com/openai/codex/blob/d42056091aded7feb1d88ac7e83972108b2aa478/codex-rs/tui/src/subscription.rs

Authenticated usage/catalog observations confirmed `promax`, a 604800-second
primary-slot window with no secondary window, and Astra service tiers
`priority` and `ultrafast`. A Pro control advertised only `priority` for
Astra. Both advertised context 272000 and the same reasoning efforts.
The usage payload supplied percentages, reset times and a separate purchased
credit balance, but no absolute included allowance. `chatpass.windows`
repeated the weekly percentage; `additional_rate_limits` was null.

No absolute included-credit allowance was established. Relative plan
multipliers can inform estimates against a chosen reference, but do not
provide that absolute allowance. The Pro tiers documentation also distinguishes
grandfathered and reduced Pro 200 allowances under the same plan identifier.
Subscription price and purchased credits are therefore not reliable allowance
conversions. Ultrafast's 8x included-usage / 6x purchased-credit consumption is
not a plan multiplier.

## Decision

Keep identity, subscription allowance and routing weight separate. Pro Max
has unknown absolute subscription capacity. Existing numeric aggregate
fields remain legacy estimated-credit subtotals, with explicit coverage
metadata. The existing Pro reference weight is used only by routing, so an
upgraded account is not treated as Free or zero-capacity.

Calibrating relative capacity estimates and modeling transitional Pro allowance
cohorts are separate changes. This change keeps the existing estimates for
other plans and makes the missing Max coverage explicit.

For example, Pro at 40% used and Plus at 50% used contribute 57960 estimated
weekly capacity and 34020 remaining credits. A Pro Max account at 20% used
adds one unquantified account, not an invented number of credits. Its own
remaining percentage is 80%; the whole pool's weighted remaining percentage
and credit-based forecast are unavailable.

## Wire contract

- Usage window: `unquantifiedAccountCount`, default 0.
- Per-account usage history/window: `capacityKnown`, default true.
- Pooled usage: primary/secondary unquantified-account counts,
  camelCase in dashboard responses and snake_case in OpenAI-compatible
  responses, matching the owning response conventions.
- `/v1/usage`: `upstream_limits_unquantified_windows`, containing reported
  durations (`5h`, `7d`, `30d`) only when upstream-limit disclosure is enabled.

Counts cover reported normalized Pro Max windows only. An absent short
window is not an unquantified short window. Assignment scope, account status
and privacy filters apply before counts are computed.

## Failure modes and boundaries

Catalog absence or tier exclusion is not overcome by a local plan label.
Existing account-specific catalog evidence stays authoritative. Unknown
absolute capacity must not become zero availability, a complete mixed-pool
percentage, or a frontend fallback fleet forecast. Purchased credits retain
their separate meaning. The authenticated probe required re-login because
an old refresh token had been revoked; plan recognition does not repair
revoked authentication.
