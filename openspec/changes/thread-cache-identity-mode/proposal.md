# Add shared/isolated thread cache identity modes

## Why

Every outbound request the load balancer makes today presents the same
cacheable content prefix regardless of which upstream account it goes out on.
Direct probes against this deployment show what that means in practice: a
~28,000-token prefix carrying a fresh nonce, seeded by one account, produced
near-full cache hits on four *other* accounts (28,032/28,168 · 27,136/27,328 ·
28,416/28,588), reproduced across three runs. The same probes ruled out the two
obvious explanations — a hit landed with a *different* `prompt_cache_key`, and
several misses landed with the *same* one, so the key does not gate the cache;
same-account repeats hit 1/5 while other accounts hit 3/8, so the account does
not gate it either. The behaviour is consistent with a node-local prefix cache
that is not partitioned per account.

There is no way to turn that off, and there is no way to get deterministic
per-account cache behaviour for a single API key while the rest of the fleet is
left alone. This change adds the switch.

**Purpose, stated plainly: this mode exists for cache isolation and
deterministic per-account cache behaviour.** Whatever it does to how
correlatable the pool looks to the provider is a side effect, and this
deployment's own data does not support acting on it — the incidents that
prompted the investigation were capacity allocation, not detection. Nobody
should later justify flipping the default on a rationale the data does not
carry.

## What Changes

- A T3 setting `thread_cache_identity_mode` with the values `shared` and
  `isolated`, defaulting to `shared`, with a nullable `dashboard_settings`
  column seeded NULL and an environment fallback.
- A nullable `api_keys.thread_cache_identity_override` so one key can run
  `isolated` while the fleet stays `shared`. The key override wins over the
  dashboard value, which wins over the environment value, which loses to
  nothing.
- In `isolated` mode, three legs of the outbound request are scoped per
  upstream account: the `prompt_cache_key`, the Codex process-session /
  conversation / thread headers, and a stable opaque line prepended to the
  prompt content. The scope token is
  `sha256("codex-lb-cache-scope-v1|" + <load-balancer account id>)[:16]` —
  deterministic, no clock, no randomness, identical on every turn of every
  session on that account.
- In `shared` mode the outbound request is byte-for-byte what it is today.

## What isolation buys, and what it costs

Only the **content prefix** isolates anything. Namespacing the
`prompt_cache_key` is cache-neutral, because the measurements show hits are not
key-gated; it is bookkeeping that keeps the wire identity consistent with the
content identity. **`prompt_cache_key` scoping alone does not isolate
anything**, and this change must never be described as though it does.

The cost is concrete and is the expected outcome, not a bug. Cross-account
prefix sharing is currently a *benefit*: a thread that fails over from account
A to account B still hits A's warm prefix, worth roughly **27,000-28,000 input
tokens per hit**. That is why keyed traffic holds ~91% cache despite 8-26%
turn-to-turn account switching. Turning it off spends those tokens again.

How much it costs depends on the accounts-per-conversation factor, which on
this deployment is driven by failover and soft-sticky rerouting under upstream
pressure rather than by any structural defect:

| day | conversations (>=3 requests) | mean accounts | single-account share |
|---|---|---|---|
| upstream healthy | 1,929 | 2.29 | 45% |
| upstream incident | 2,674 | 3.45 | 42% |
| upstream quiet | 326 | **1.02** | **99%** |

At 2.29-3.45 accounts per conversation, `isolated` is a net loss. **It should
only be enabled once the factor is near 1.** That is why the default is
`shared` and why the per-key override exists: the mode is measurable on one key
before anyone considers the fleet.

Isolation is also bounded by the provider's prompt assembly, which this project
does not control. If upstream serializes tools or other identical fields ahead
of the injected line, a common leading block still matches and isolation is
partial. That is an empirical question the direct probe answers; this proposal
does not claim total isolation.

## Non Goals

- Changing the default. `shared` stays the default until measurement says
  otherwise.
- Presenting the mode as a countermeasure to provider-side correlation. It is
  not offered, documented or justified as one.
- Touching `x-codex-turn-state` or `previous_response_id` in either mode. Both
  are upstream-issued values that round-trip verbatim, and both are already
  owner-bound, so neither carries anything across accounts.
- Deriving any part of the scope token from a session identifier. The most
  specific session value the proxy holds is `x-codex-turn-state`, which is
  minted fresh per turn when the client does not supply one; hashing it would
  rotate the prefix every turn and destroy caching *inside* an account, which
  is strictly worse than either mode. 33-38% of requests carry no session
  identifier at all, so a session component has no stable value for a third of
  traffic either. The token is account-only, and the granularity is a code
  constant rather than a knob.
- A dashboard UI control. The mode is reachable through `PUT /api/settings` and
  the API-keys API; no bespoke effective-value hint is added anywhere.
- The HTTP session bridge and the direct downstream WebSocket surface. Both
  serialize the request text *before* an account is chosen and then treat that
  exact text as a dispatch-owner and replay key, so scoping either means
  re-preparing the text after account selection against the
  stored-input-context, input-fingerprint and size-guard invariants that
  re-preparation already has to respect. That is its own change with its own
  tests.

  So this change lands the mechanism, the configuration, the safe default and
  the coverage for the core upstream client (HTTP streaming, non-streaming
  HTTP, the upstream websocket `response.create`) plus compact. The bridge is
  enabled by default, so an operator who flips `isolated` today will see it
  apply to less HTTP traffic than the word "HTTP" suggests. That is stated
  here and in the spec rather than papered over, and it is the reason the
  measurement plan starts with a direct probe rather than a production A/B.

## Capabilities

### Added Capabilities

- `responses-api-compat`: the thread cache identity mode and its three scoped
  legs, the shape-aware injection point, prefix stability, and the
  byte-identical `shared` guarantee.
- `api-keys`: the per-key `thread_cache_identity_override` and its precedence.

### Modified Capabilities

- `responses-api-compat`: two requirements state in normative text that
  `prompt_cache_key` is forwarded upstream **unchanged**. `isolated` mode
  contradicts both literally, so both clauses are conditioned on `shared`.

## Impact

- Schema: two nullable columns (`dashboard_settings.thread_cache_identity_mode`,
  `api_keys.thread_cache_identity_override`), one migration, no backfill. An
  upgrade introduces no decision and changes no behaviour.
- Configuration: one new `Settings` field, `[settings_fields].max` 96 -> 97.
- Runtime: in `shared` mode every helper returns before touching anything, so
  the request path is unchanged and the golden-snapshot tests pin that.
