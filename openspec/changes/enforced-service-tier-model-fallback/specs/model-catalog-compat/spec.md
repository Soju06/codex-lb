## MODIFIED Requirements

### Requirement: Complete account catalogs constrain pooled routing

The system MUST retain the union of successfully refreshed account model
catalogs for client discovery. When every active account has a current or
retained last-known catalog, request selection MUST route a model or explicit
non-default service tier only to accounts whose own catalog advertised that
capability. Requests that omit a tier or use the omit-equivalent `auto` or
`default` tiers MUST use model-only account filtering, including when reusing
an HTTP bridge session.

A service tier imposed by an API key's enforced service tier is not an explicit
request for that tier. When the requested tier originates from API key
enforcement and the model's catalog does not advertise that tier at all, the
system MUST remove the tier from the account-routed request, MUST select
accounts and reuse HTTP bridge sessions using model-only filtering, MUST
reserve, settle, and log API-key usage at the effective default tier, and MUST
omit the unsupported tier from the upstream request. This account-catalog
fallback MUST NOT alter a request selected for an external model source, and an
unknown or account-catalog-absent model MUST retain the enforced tier. When the model's catalog
does advertise the tier, account-level tier filtering MUST continue to apply
regardless of the tier's origin. A tier supplied explicitly by the client MUST
continue to filter accounts even when it equals the enforced value or uses an
equivalent alias and the model does not advertise it. When an
unavailable service tier is what excluded every account, the selection error
MUST name that tier.

#### Scenario: Same-plan accounts expose different models

- **GIVEN** two active accounts share a plan
- **AND** only one account advertises a model
- **WHEN** all active account catalogs are known
- **THEN** the merged discovery catalog includes the model
- **AND** requests for that model select only the advertising account

#### Scenario: Same-plan accounts expose different Fast tiers

- **GIVEN** two active accounts advertise the same model
- **AND** only one advertises the priority service tier
- **WHEN** a request explicitly asks for priority
- **THEN** selection considers only the account that advertised priority

#### Scenario: Enforced tier does not exclude a model that never advertises it

- **GIVEN** an active account advertises a model at its default tier
- **AND** the model's catalog advertises no `priority` service tier
- **AND** an API key sets `enforced_service_tier` to `priority`
- **WHEN** account selection is requested for that model
- **THEN** the enforced tier is removed from the account-routed request
- **AND** API-key accounting, bridge compatibility, and upstream forwarding use the effective default tier
- **AND** the advertising account is selected

#### Scenario: Account-catalog fallback does not alter a model source

- **GIVEN** an API key enforces the `priority` service tier
- **AND** the selected model is routed through an external model source
- **WHEN** the subscription-account catalog does not advertise `priority` for that model
- **THEN** the source-routed request retains `priority`

#### Scenario: Explicitly requested unadvertised tier is still rejected

- **GIVEN** an active account advertises a model at its default tier
- **AND** the model's catalog advertises no `priority` service tier
- **WHEN** a client explicitly requests that model with `priority` or an equivalent `fast` alias
- **THEN** no account is selected

#### Scenario: Unavailable advertised tier names the tier in the error

- **GIVEN** a model's catalog advertises the `priority` service tier
- **AND** no active account carries `priority` for that model
- **WHEN** account selection is requested for that model with `priority`
- **THEN** no account is selected
- **AND** the selection error names the `priority` service tier
