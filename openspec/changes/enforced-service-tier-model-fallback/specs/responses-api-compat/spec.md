## MODIFIED Requirements

### Requirement: API key service tier enforcement applies to upstream Responses requests

When an API key carries an enforced service tier, the proxy MUST override any
incoming Responses request service tier with that enforced value before route
selection. The omit-equivalent client values `auto` and `default` MUST count as
an omitted tier when tracking whether the enforced value supplied the request's
tier. The legacy alias `fast` MUST be treated as `priority`.

For a subscription-account route, when an authoritative account catalog says
the selected model never advertises the enforced tier, the proxy MUST remove
that tier from the effective request before account selection and upstream
forwarding. The resulting effective tier MUST survive internal owner
forwarding unchanged. This fallback MUST NOT remove an explicit non-default
client tier, MUST NOT alter a request routed through an external model source,
and MUST NOT apply when the account catalog has no authoritative answer for the
model.

#### Scenario: Enforced service tier overrides the request payload

- **GIVEN** the selected account model advertises the `priority` service tier
- **WHEN** an API key is configured with `enforcedServiceTier: "priority"`
- **AND** an incoming Responses request asks for `service_tier: "default"`
- **THEN** the forwarded upstream payload uses `service_tier: "priority"`

#### Scenario: Omit-equivalent request permits account-catalog fallback

- **GIVEN** an account model authoritatively advertises no `priority` service tier
- **WHEN** an API key is configured with `enforcedServiceTier: "priority"`
- **AND** an incoming Responses request omits `service_tier` or supplies `auto` or `default`
- **THEN** the account-routed upstream payload omits `service_tier`
- **AND** an internal owner forward preserves that effective omission

#### Scenario: Explicit non-default tier is not downgraded

- **GIVEN** an account model authoritatively advertises no `priority` service tier
- **WHEN** a client explicitly requests `service_tier: "priority"` or the equivalent `fast` alias
- **THEN** API-key enforcement does not make the tier eligible for account-catalog fallback

#### Scenario: Fast alias is applied as priority

- **WHEN** an API key is configured with `enforcedServiceTier: "fast"`
- **THEN** the forwarded upstream payload uses the canonical value `priority`
