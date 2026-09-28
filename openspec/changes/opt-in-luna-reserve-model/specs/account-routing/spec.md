## MODIFIED Requirements

### Requirement: Additional quota routing policy

Each known additional quota MAY have a routing policy of `inherit`, `normal`, `burn_first`, `preserve`, or `disabled`. `inherit` SHALL use the selected account's routing policy. `normal`, `burn_first`, and `preserve` SHALL override account routing policy for requests gated by that additional quota. `disabled` is not a ranking policy: it SHALL remove the quota from routing entirely, and a request gated by a `disabled` quota SHALL be refused rather than ranked.

A routing policy value that is not one of these SHALL normalize to `inherit` when read from the registry. When read from a dashboard override, an unrecognized value SHALL instead be ignored, so the quota falls back to its registry default rather than to a routable policy. A quota whose registry entry declares `disabled` therefore SHALL be rejected by routing until an operator overrides it with a recognized routable policy, and MUST NOT silently become routable — including through a malformed override.

Because `disabled` is not an account routing policy, it MUST NOT be propagated into an account's effective routing policy for a selection. The ranking-override path SHALL treat `disabled` the same as `inherit` and emit no override.

The refusal SHALL be evaluated before account filtering for that quota, so that no later fallback — including the rescue that reconsiders accounts when every candidate requires re-authentication — can route a quota an operator has switched off. The refusal SHALL carry error code `additional_quota_routing_disabled` and a message naming the quota's display label so the operator can find the control that enables it.

For additional-quota-gated requests, account selection SHALL use fresh additional-quota usage windows for budget and reset comparison and SHALL NOT reject an account solely because its standard 5h or 7d quota is exhausted.

#### Scenario: additional quota inherits account policy

- **GIVEN** an additional quota has routing policy `inherit`
- **WHEN** the load balancer selects an account for that additional quota
- **THEN** it applies the account's own routing policy

#### Scenario: additional quota override takes precedence

- **GIVEN** an additional quota has routing policy `burn_first`
- **AND** an account with fresh available quota for that additional quota has standard Codex quota exhausted
- **WHEN** the load balancer selects an account for that additional quota
- **THEN** the account remains eligible and is treated as `burn_first` for that selection

#### Scenario: a disabled additional quota is refused at selection

- **GIVEN** an additional quota whose effective routing policy is `disabled`
- **AND** an account with fresh available quota in that bucket
- **WHEN** the load balancer selects an account for a model gated by that quota
- **THEN** no account is selected
- **AND** the failure carries error code `additional_quota_routing_disabled`
- **AND** the message names the quota's display label

#### Scenario: an operator override makes a disabled quota routable

- **GIVEN** an additional quota whose registry entry declares `disabled`
- **AND** a dashboard override sets that quota's policy to `normal`
- **WHEN** the load balancer selects an account for a model gated by that quota
- **THEN** an account with fresh available quota in that bucket is selected

#### Scenario: a disabled quota is not rescued by the reauthentication fallback

- **GIVEN** an additional quota whose effective routing policy is `disabled`
- **AND** every candidate account requires re-authentication
- **WHEN** the load balancer selects an account for a model gated by that quota
- **THEN** the request is refused as `additional_quota_routing_disabled`
- **AND** no account is returned by the re-authentication rescue

#### Scenario: a disabled policy never becomes an account routing policy

- **GIVEN** an additional quota whose effective routing policy is `disabled`
- **WHEN** the selector resolves the ranking override for that quota
- **THEN** no routing-policy override is produced
- **AND** candidate accounts keep their own routing policy

## ADDED Requirements

### Requirement: Luna Reserve is an operator-enabled model, not an automatic fallback

Upstream reports an eligible Plus or Pro account's Luna Reserve allowance as an additional rate limit with `metered_feature` `base_model_inference` and `limit_name` `gpt-reserve`. codex-lb SHALL make this allowance reachable only by an explicit request for the `gpt-reserve` model, and SHALL register that model against the `base_model_inference` additional quota so that selection reads the reserve window instead of the account's standard quota.

The reserve quota SHALL ship with routing policy `disabled`, so a default installation does not route to it. The usage fetch SHALL send `supportsLunaReserve=true` so upstream includes the bucket in `additional_rate_limits`; an upstream that does not recognize the parameter ignores it and the fetch behaves as before.

codex-lb SHALL NOT reroute a request to the reserve on its own. It MUST NOT observe standard quota exhaustion to decide the model, MUST NOT hold per-account reserve mode, MUST NOT substitute the model on the wire, and MUST NOT rewrite the model name in downstream events. A turn requested as `gpt-reserve` SHALL be dispatched as `gpt-reserve` and reported to the client as `gpt-reserve`; a turn requested as any other model SHALL never be served from the reserve.

When an account is dispatched a `gpt-reserve` turn and upstream refuses it for lack of reserve eligibility, an unrecognized refusal SHALL be classified `non_retryable` so it surfaces to the client instead of being retried across the account pool.

#### Scenario: the reserve is unreachable on a default installation

- **GIVEN** a codex-lb installation with no additional-quota routing overrides
- **AND** an eligible account reporting available `base_model_inference` quota
- **WHEN** a client requests the `gpt-reserve` model
- **THEN** the request is refused with `additional_quota_routing_disabled`

#### Scenario: an exhausted account is never rerouted to the reserve

- **GIVEN** an account whose standard chat quota is exhausted
- **AND** the same account reports available `base_model_inference` quota
- **WHEN** a client requests a standard chat model
- **THEN** the request is not served from the reserve
- **AND** the account's ordinary quota-exhaustion handling applies

#### Scenario: an enabled reserve serves an explicit request

- **GIVEN** an operator has set the `base_model_inference` quota policy to a routable value
- **AND** an eligible account reports available reserve quota with its standard quota exhausted
- **WHEN** a client requests the `gpt-reserve` model
- **THEN** that account is selected
- **AND** the upstream request carries model `gpt-reserve`
- **AND** the response reports model `gpt-reserve`

#### Scenario: an ineligible account surfaces the refusal instead of failing over

- **GIVEN** reserve routing is enabled
- **AND** the selected account is not eligible for the reserve
- **WHEN** upstream refuses the `gpt-reserve` turn with an unrecognized error
- **THEN** the failure is classified `non_retryable`
- **AND** it surfaces to the client rather than being retried on other accounts
