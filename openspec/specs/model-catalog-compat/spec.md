# model-catalog-compat Specification

## Purpose
TBD - created by archiving change populate-bootstrap-model-metadata. Update Purpose after archive.
## Requirements
### Requirement: Bootstrap model catalog is available before refresh

Before the first successful upstream model-registry refresh, the system MUST
serve a conservative static catalog of known Codex model slugs from both
`GET /v1/models` and `GET /backend-api/codex/models`. This static catalog is a
bundled fallback for startup/offline paths; refreshed upstream model-registry
data remains the authoritative source once available. The bootstrap catalog MUST
include `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`,
`gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex`, `gpt-5.3-codex-spark`,
`gpt-5.2`, and `codex-auto-review`, and MUST NOT invent unverified variant
slugs such as `gpt-5.5-pro` or a bare `gpt-5.6`. `gpt-5.3-codex` and
`gpt-5.3-codex-spark` were dropped from upstream's bundled catalog at
codex rust-v0.144.x but remain retained for older pinned clients because the
upstream backend still serves them.

#### Scenario: OpenAI-compatible models endpoint serves bootstrap slugs

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /v1/models`
- **THEN** the response contains exactly the bootstrap model slugs
- **AND** the response includes `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`
- **AND** the response does not include `gpt-5.5-pro` or bare `gpt-5.6`

#### Scenario: Codex-native models endpoint serves GPT-5.6 bootstrap metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` entries include representative upstream metadata including context-window, visibility, speed-tier, and reasoning fields
- **AND** Sol and Terra advertise `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`
- **AND** Luna advertises `low`, `medium`, `high`, `xhigh`, and `max`

### Requirement: Refreshed upstream model data remains authoritative

The system MUST treat a refreshed upstream model-registry snapshot as
authoritative over the static bootstrap catalog. Once that snapshot exists,
model catalog endpoints and model-behavior lookups MUST use the refreshed
snapshot instead of the static bootstrap catalog. Before refresh, websocket
preference lookup and account plan filtering MUST use bootstrap model metadata
when the requested slug matches a bootstrap entry.

#### Scenario: Refreshed snapshot replaces bootstrap catalog

- **GIVEN** the model registry has a refreshed upstream snapshot
- **WHEN** a client calls `GET /v1/models` or `GET /backend-api/codex/models`
- **THEN** the response is built from the refreshed snapshot
- **AND** bootstrap-only entries are not added to the response

#### Scenario: Non-authoritative refresh preserves bootstrap floor

- **GIVEN** the model registry does not have authoritative account-catalog coverage
- **WHEN** a client calls `GET /v1/models`, `GET /backend-api/codex/models`, or a routing path checks websocket preference or allowed plans for a bootstrap model that is absent from the partial snapshot
- **THEN** the system still uses the bootstrap catalog entry for that model as the discovery and plan-gating floor
- **AND** exact supporting-account routing remains unknown until authoritative account-catalog coverage exists

#### Scenario: Non-authoritative refresh does not resurrect dead bootstrap models

- **GIVEN** the latest non-authoritative snapshot knows that every last-known advertiser of a bootstrap model is inactive or removed
- **WHEN** discovery or plan gating is evaluated for that model before any authoritative refresh completes
- **THEN** the model remains absent instead of reappearing from the bootstrap floor

#### Scenario: Repeated non-authoritative refreshes keep removed bootstrap models suppressed

- **GIVEN** a non-authoritative snapshot suppressed a bootstrap model because every last-known advertiser left the active account set
- **WHEN** later refresh cycles still lack authoritative account-catalog coverage and still do not produce fresh active evidence for that model
- **THEN** the model stays absent from discovery and plan gating across those repeated refreshes

#### Scenario: Fresh active evidence clears bootstrap suppression

- **GIVEN** a bootstrap model was previously suppressed after its last-known advertisers left the active account set
- **WHEN** a later refresh records that an active account advertises that model again
- **THEN** the suppression is cleared
- **AND** the model returns to discovery and plan gating from live registry data

#### Scenario: Bootstrap websocket preference is honored before refresh

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** websocket preference is checked for a bootstrap model marked as websocket-preferred
- **THEN** the lookup returns that bootstrap preference

#### Scenario: Bootstrap plan metadata filters accounts before refresh

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** a bootstrap model excludes a plan from its plan-availability metadata
- **WHEN** account selection is requested for that bootstrap model
- **THEN** accounts on excluded plans are not selected for that model

### Requirement: OpenAI-compatible model metadata uses backend context windows

When serving `GET /v1/models`, the system SHALL expose `metadata.context_window` as the upstream backend `context_window` budget by default. The system MUST NOT promote raw `max_context_window` values or hard-coded full-context guesses into `metadata.context_window`. Explicit operator context-window overrides remain the highest-priority reported-context value.

#### Scenario: GPT-5 Codex models are reported with the backend context window on /v1/models

- **WHEN** the upstream model catalog contains `gpt-5.5`, `gpt-5.4-mini`, `gpt-5.3-codex`, or `gpt-5.4` with `context_window=272000`
- **THEN** `GET /v1/models` returns each entry with `metadata.context_window=272000`

#### Scenario: raw max_context_window does not inflate /v1/models context_window

- **WHEN** the upstream model catalog contains a model with `context_window=272000` and `max_context_window=900000`
- **THEN** `GET /v1/models` returns that entry with `metadata.context_window=272000`

### Requirement: OpenAI-compatible model metadata preserves the backend input budget explicitly

When serving `GET /v1/models`, the system SHALL expose the upstream backend input/context budget in `metadata.input_context_window`. For models whose reported `metadata.context_window` is not operator-overridden, `metadata.context_window` and `metadata.input_context_window` SHOULD be equal. The system SHOULD expose `metadata.max_output_tokens` for known GPT-5 Codex models when that output-budget value is known; that value MUST NOT be used to inflate `metadata.context_window`.

#### Scenario: /v1/models exposes the 272k backend input budget explicitly

- **WHEN** the upstream model catalog contains a known GPT-5 Codex model with `context_window=272000`
- **THEN** `GET /v1/models` returns that model with `metadata.input_context_window=272000`
- **AND** `metadata.context_window=272000`

#### Scenario: Explicit reported-context overrides do not hide the backend input budget

- **WHEN** an operator override sets a model's reported `metadata.context_window` to `515000`
- **AND** the upstream model catalog contains that model with `context_window=272000`
- **THEN** `GET /v1/models` returns that model with `metadata.context_window=515000`
- **AND** `metadata.input_context_window=272000`

#### Scenario: /v1/models exposes max output budget for known GPT-5 Codex models

- **WHEN** `GET /v1/models` returns `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, or `gpt-5.3-codex`
- **THEN** the entry's metadata includes `max_output_tokens=128000`

### Requirement: Codex-native model catalog keeps backend catalog fields

When serving `GET /backend-api/codex/models`, the system MUST keep Codex-native model catalog semantics unchanged: the top-level `context_window` field remains the backend compact/input budget unless an explicit operator override applies, and upstream raw fields such as `max_context_window` remain available when upstream provides them. The `/v1/models` compatibility metadata MUST NOT mutate the native Codex endpoint.

#### Scenario: Native Codex route preserves compact budget

- **WHEN** the upstream model catalog contains `gpt-5.5` with `context_window=272000`
- **THEN** `GET /backend-api/codex/models` returns `gpt-5.5.context_window=272000`
- **AND** it does not replace that field with `400000`

#### Scenario: Codex model catalog also exposes OpenAI data alias

- **WHEN** a client requests `GET /backend-api/codex/models`
- **THEN** the response keeps the Codex-native `models` list
- **AND** the response includes `object: "list"` and an OpenAI-compatible `data` list
- **AND** `data` contains model entries whose Codex visibility is `list`
- **AND** `data` excludes entries whose Codex visibility is `hide`

### Requirement: OpenAI-compatible model metadata preserves speed tiers

When serving `GET /v1/models`, the system SHALL preserve upstream speed-tier metadata in each model's `metadata` object when upstream provides it. This includes `additional_speed_tiers`, `service_tiers`, and `default_service_tier`. The system MUST NOT invent speed tiers for models whose upstream catalog entry does not advertise them.

#### Scenario: /v1/models exposes upstream fast tier metadata

- **WHEN** the upstream model catalog contains `gpt-5.5` with `additional_speed_tiers=["fast"]`
- **AND** the upstream model catalog includes a `service_tiers` entry with `id="priority"` and `name="Fast"`
- **WHEN** a client calls `GET /v1/models`
- **THEN** the `gpt-5.5` entry's metadata includes `additional_speed_tiers=["fast"]`
- **AND** the metadata includes the upstream `service_tiers` entry
- **AND** the metadata includes the upstream `default_service_tier` when present

### Requirement: GPT-5.6 bootstrap metadata matches the upstream bundled catalog

The GPT-5.6 bootstrap catalog entries (`gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`) MUST mirror the upstream bundled catalog (`codex-rs/models-manager/models.json` at codex release rust-v0.144.1) field-for-field for every metadata field codex-lb serves. In particular each
entry MUST carry: `context_window` and `max_context_window` of `372000`;
`minimal_client_version` `"0.144.0"`; `tool_mode` `"code_mode_only"`;
`use_responses_lite` `true`; `apply_patch_tool_type` `"freeform"`;
`web_search_tool_type` `"text_and_image"`; `supports_image_detail_original`
`true`; `truncation_policy` `{"mode": "tokens", "limit": 10000}`;
`comp_hash` `"3000"`; `reasoning_summary_format` `"experimental"`;
`default_reasoning_summary` `"none"`; `include_skills_usage_instructions`
`false`; `experimental_supported_tools` `[]` (a field the Codex client's
deserializer requires); `supports_search_tool` `true`; `additional_speed_tiers`
`["fast"]`; the `priority`/`Fast` service tier entry; `shell_type`
`"shell_command"`; `prefer_websockets` `true`; and the 21-plan
`available_in_plans` list upstream advertises (including `edu_plus`,
`edu_pro`, `enterprise_cbp_automation`, and `sci`). `multi_agent_version` MUST
be `"v2"` for Sol and Terra and `"v1"` for Luna. Sol MUST carry the upstream
`availability_nux` message while Terra and Luna carry `null`. Default
reasoning levels MUST be `low` for Sol and `medium` for Terra and Luna, and
reasoning-level descriptions MUST be the verbatim upstream strings.

The ~16.5 KB upstream `base_instructions` prompt and the personality-templated
`model_messages` object are deliberately NOT bundled in the bootstrap catalog;
the first successful live registry refresh supplies them. This is the only
sanctioned divergence from the upstream GPT-5.6 entries.

#### Scenario: GPT-5.6 entries expose upstream tool and multi-agent metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` carry `tool_mode: "code_mode_only"`, `use_responses_lite: true`, `experimental_supported_tools: []`, and `minimal_client_version: "0.144.0"`
- **AND** `multi_agent_version` is `"v2"` for Sol and Terra and `"v1"` for Luna

#### Scenario: GPT-5.6 entries expose upstream reasoning-summary and plan metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** each GPT-5.6 entry carries `default_reasoning_summary: "none"`, `reasoning_summary_format: "experimental"`, and `comp_hash: "3000"`
- **AND** each GPT-5.6 entry's `available_in_plans` includes `edu_plus`, `edu_pro`, `enterprise_cbp_automation`, and `sci`
- **AND** only `gpt-5.6-sol` carries a non-null `availability_nux` message

### Requirement: Fallback client version covers the bootstrap catalog

The configured fallback Codex client version (used when the live Codex release lookup fails and no cached version exists) MUST be greater than or equal to the highest `minimal_client_version` in the bootstrap catalog, so a degraded-startup registry refresh still receives the newest bootstrap models from upstream.

#### Scenario: Degraded-startup refresh still requests GPT-5.6

- **GIVEN** the live Codex release lookup fails and no version is cached
- **WHEN** the model registry refresh fetches `<base>/codex/models?client_version=<fallback>`
- **THEN** the fallback version is at least `0.144.0` (GPT-5.6's `minimal_client_version`)

### Requirement: Dashboard model metadata exposes supported reasoning efforts

When serving `GET /api/models`, the system MUST expose the supported reasoning
efforts advertised by each public model catalog entry. The response MUST include
new upstream-supported efforts such as `max` and `ultra` instead of filtering
them out.

#### Scenario: Dashboard model list exposes GPT-5.6 reasoning efforts

- **WHEN** the model catalog contains `gpt-5.6-sol` with supported efforts `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`
- **WHEN** a client calls `GET /api/models`
- **THEN** the `gpt-5.6-sol` entry's `supportedReasoningEfforts` includes `max` and `ultra`
- **AND** `defaultReasoningEffort` reflects the catalog default

### Requirement: Source-model Codex catalog entries are Codex-parseable

Codex catalog entries built for OpenAI-compatible source models MUST be
parseable by Codex clients without relying on bundled metadata. When the
source model has no configured context window, the entry MUST report a
context window of 128,000 tokens and a matching `max_context_window`. The
entry MUST include `shell_type` (`shell_command`), a `truncation_policy`,
and the client-capability fields `include_skills_usage_instructions`,
`supports_image_detail_original`, `supports_search_tool`,
`use_responses_lite`, and `experimental_supported_tools`, defaulting each to
its most conservative value. Operator-provided values for these keys in the
source model's `raw_metadata_json` MUST take precedence over the defaults.

#### Scenario: Source model without a context window gets the default budget

- **GIVEN** an enabled Responses-capable source model with no `contextWindow` configured
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the model's entry reports `context_window` of 128000
- **AND** `max_context_window` of 128000
- **AND** `shell_type` of `shell_command`
- **AND** conservative defaults for the client-capability fields (for example `supports_search_tool` is `false` and `use_responses_lite` is `false`)

#### Scenario: Operator capability opt-in overrides the defaults

- **GIVEN** a source model whose `raw_metadata_json` sets `"supports_search_tool": true`
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the model's entry reports `supports_search_tool` as `true`

### Requirement: Source request overrides never appear in client-visible catalogs

The per-model `source_request_overrides` object in a source model's `raw_metadata_json` is operator-side request configuration and MUST NOT
appear in any client-visible catalog payload (`GET /backend-api/codex/models`,
`GET /v1/models`, or any equivalent catalog route), while remaining available
server-side for request override application.

#### Scenario: Override config is stripped from the Codex catalog

- **GIVEN** a source model whose `raw_metadata_json` contains `"source_request_overrides": {"options": {"num_ctx": 32768}}`
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the model's catalog entry does not contain a `source_request_overrides` key
- **AND** the string `source_request_overrides` appears nowhere in the response payload

#### Scenario: Overrides still apply to forwarded requests

- **GIVEN** the same source model
- **WHEN** a Responses request is forwarded to the source
- **THEN** the forwarded payload includes the configured override values

### Requirement: Upstream model_messages is preserved in the catalog

When parsing upstream model-registry data, the system MUST preserve the
`model_messages` field on each model entry through to the Codex-native catalog
response. The field MUST NOT be stripped during fetch parsing, registry
storage, or catalog serialization. `GET /backend-api/codex/models` and
`GET /v1/models?client_version=<v>` MUST return each model's `model_messages`
object unchanged from the upstream response once a refreshed registry snapshot
exists.

#### Scenario: model_messages survives the fetch → registry → catalog path

- **GIVEN** the upstream model catalog contains a model with a `model_messages` object
- **WHEN** the model registry refresh parses the upstream response
- **THEN** the resulting `UpstreamModel.raw` includes `model_messages` unchanged

#### Scenario: Codex-native catalog endpoint returns model_messages

- **GIVEN** the model registry has a refreshed snapshot containing a model with `model_messages`
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the model entry in the response includes `model_messages` with the same value as the upstream response

#### Scenario: OpenAI-compatible catalog endpoint returns model_messages for Codex clients

- **GIVEN** the model registry has a refreshed snapshot containing a model with `model_messages`
- **WHEN** a client calls `GET /v1/models?client_version=<v>`
- **THEN** the model entry in the response includes `model_messages` with the same value as the upstream response

### Requirement: Codex metadata survives a partial live catalog refresh

The proxy MUST retain the last successfully fetched complete metadata for a
bundled Codex model when a later successful live catalog refresh omits that
model. A retained model that is absent from the current live availability snapshot MUST be
returned through the Codex model catalog with hidden visibility so an explicitly
configured client can resolve its metadata without advertising it in the model
picker.

Retained metadata MUST NOT add the model to current plan, account, service-tier,
routing, dashboard, warmup, or `/v1/models` availability. A current live entry
MUST replace the retained entry when the model appears again.
Models outside the bundled Codex catalog MUST NOT be retained after they leave
the current live availability snapshot.

OpenAI-compatible source entries that share a slug with retained Codex metadata
MUST replace retained metadata only when the source entry's effective Codex
visibility is `list` for the requesting client. A same-slug source entry hidden
by raw catalog visibility or by the API key's exact source allowlist MUST NOT
shadow the retained metadata.

#### Scenario: Sol metadata remains resolvable after a partial refresh

- **GIVEN** a successful live catalog refresh returned complete metadata for `gpt-5.6-sol`
- **WHEN** a later successful refresh omits `gpt-5.6-sol`
- **THEN** the Codex catalog includes the last complete Sol metadata with hidden visibility
- **AND** `/v1/models` and live availability indexes omit Sol

#### Scenario: A later live entry replaces retained metadata

- **GIVEN** metadata was retained for a model omitted by a previous refresh
- **WHEN** a later live refresh returns that model with updated metadata
- **THEN** the updated live metadata is used and the model follows its current live visibility

#### Scenario: Hidden source entry does not replace retained metadata

- **GIVEN** metadata was retained for `gpt-5.6-sol`
- **AND** an OpenAI-compatible source exposes the same `gpt-5.6-sol` slug
- **AND** that source entry is hidden from the effective Codex catalog by raw visibility or an API key's exact source allowlist
- **WHEN** a client calls `GET /backend-api/codex/models` with that API key
- **THEN** the hidden Sol catalog entry uses the retained Codex metadata

#### Scenario: Visible same-slug source follows an earlier hidden source

- **GIVEN** multiple enabled sources expose the same model slug
- **AND** an earlier source is hidden while a later source is list-visible
- **WHEN** the Codex catalog is rendered
- **THEN** the list-visible source entry MUST take precedence for that slug
- **AND** the earlier hidden source MUST NOT suppress or replace it

#### Scenario: Bundled model appears on only one account in a plan

- **GIVEN** a same-plan refresh returns a bundled Codex model from one successful account but omits it from another
- **WHEN** the availability intersection excludes that model
- **THEN** the model MUST remain absent from live availability indexes
- **AND** its complete per-account live entry MUST refresh the metadata-only catalog

### Requirement: Complete account catalogs constrain pooled routing

The system MUST retain the union of successfully refreshed account model
catalogs for client discovery. When every active account has a current or
retained last-known catalog, request selection MUST route a model or explicit
non-default service tier only to accounts whose own catalog advertised that
capability. Requests that omit a tier or use the omit-equivalent `auto` or
`default` tiers MUST use model-only account filtering, including when reusing
an HTTP bridge session.

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

### Requirement: Unknown account catalogs degrade without false exclusion

The system MUST distinguish an account catalog that successfully omitted a
capability from an account catalog that could not be fetched. If any active
account has neither a current nor retained last-known catalog, account-level
capability indexes MUST NOT be treated as authoritative and selection MUST use
the existing plan-level fallback. Operator-mapped model slugs MUST NOT be
rejected solely because they are absent from subscription catalog discovery.
An otherwise authoritative snapshot whose account set does not cover every
currently selectable account MUST likewise degrade to plan-level routing until
account catalog coverage catches up.

When there is no authoritative account coverage — including partial refreshes
after prior successful cycles and when every account is removed and live
capability state is cleared — the static bootstrap catalog MUST remain the
discovery and plan-gating floor. Clearing capability state MUST NOT publish an
authoritative-empty catalog that reports canonical models as absent;
otherwise, in the window after an account is added but before the next
scheduled refresh, model/plan filtering would be skipped (an unsupported plan
could be selected) and `/v1/models` would report no models.

Carrying a plan's catalog forward when its refresh does not complete MUST NOT
re-advertise a model that no currently-active account of that plan advertises,
per the last-known per-account catalogs. This drop invariant MUST hold
regardless of whether the previous snapshot was authoritative: the authoritative
distinction governs whether per-account routing is trusted, not whether a dead
model is dropped from discovery. When a carried-forward model has no per-account
provenance at all (an older or plan-only snapshot that never captured per-account
catalogs), the system MUST preserve it rather than drop it, degrading safe when a
model cannot be attributed to any account.

A retained account catalog MUST remain associated with the plan type that
produced it. If an active account changes plan type and its new catalog refresh
fails, the system MUST leave that account's catalog unknown rather than
re-labeling its old capabilities as support for the new plan. Any previously
advertised catalog slug explicitly suppressed because all its known advertisers
left the active set MUST still enter plan filtering and select no account,
whether or not it is part of the static bootstrap catalog; this is distinct from
an operator-mapped slug that has no catalog evidence at all.

#### Scenario: Catalog fetch partially fails after restart

- **GIVEN** there is no previous registry snapshot
- **AND** one active account catalog refresh succeeds while another fails
- **WHEN** selection evaluates a model or service tier
- **THEN** the partial index is non-authoritative
- **AND** the failed account is not classified as lacking every capability

#### Scenario: No active accounts fall back to the bootstrap floor

- **GIVEN** live capability state is cleared because no active accounts remain
- **WHEN** an account is added before the next scheduled refresh completes
- **THEN** canonical bootstrap models remain discoverable via `/v1/models`
- **AND** those models remain plan-gated by the bootstrap catalog
- **AND** an account whose plan does not support the model is not selected

#### Scenario: Failed refresh has last-known account data

- **GIVEN** every active account had a successful earlier catalog
- **AND** one account fails a later refresh
- **WHEN** that account remains active
- **THEN** its last-known capability data is retained
- **AND** the complete snapshot remains authoritative

#### Scenario: Successful empty catalog withdraws stale capabilities

- **GIVEN** an active account previously advertised a model
- **AND** its later catalog refresh succeeds with an empty model list
- **WHEN** the next registry snapshot is built
- **THEN** the empty catalog is treated as successful account coverage
- **AND** the previously advertised model leaves discovery and exact routing

#### Scenario: Metadata-only account model stays unroutable during partial refresh

- **GIVEN** an account catalog contains a model omitted from the plan discovery catalog
- **AND** a later refresh retains that account's stale catalog
- **WHEN** the next registry snapshot is built
- **THEN** the metadata-only model does not enter model, plan, account, or service-tier routing indexes

#### Scenario: Fresh metadata-only model stays out of routing indexes

- **GIVEN** a refreshed account catalog contains a model omitted from the merged discovery catalog
- **WHEN** the registry builds account and service-tier routing indexes
- **THEN** the metadata-only model does not enter either routing index

#### Scenario: Selectable account set is newer than registry coverage

- **GIVEN** an authoritative registry snapshot covers the previously selectable accounts
- **AND** a newly imported or reactivated account becomes selectable before the next catalog refresh
- **WHEN** request selection evaluates model or service-tier support
- **THEN** account-level indexes are treated as incomplete
- **AND** selection degrades to plan-level routing

#### Scenario: Bridge owner is newer than registry coverage

- **GIVEN** an HTTP bridge session belongs to a selectable account absent from the registry snapshot
- **WHEN** a compatible follow-up evaluates model or service-tier support
- **THEN** stale account-level indexes do not detach the bridge owner
- **AND** compatibility degrades to plan-level routing

#### Scenario: Failed refresh follows an account plan-type change

- **GIVEN** an account previously advertised a catalog while on one plan type
- **AND** the active account record now has a different plan type
- **AND** its catalog refresh fails in that cycle
- **WHEN** the next registry snapshot is built
- **THEN** the prior catalog is not retained for that account
- **AND** the account remains unknown until a catalog for its current plan is fetched

#### Scenario: Account is paused or removed

- **GIVEN** an account has retained catalog capabilities
- **WHEN** it is no longer in the active account set
- **THEN** its capabilities no longer contribute to discovery or routing

#### Scenario: Removed account is the sole advertiser within a stale plan

- **GIVEN** two accounts share a plan and only one advertised a given model
- **AND** the plan's refresh does not complete this cycle, so its catalog is carried forward
- **AND** the sole advertiser is no longer in the active account set
- **AND** the other account of that plan remains active
- **WHEN** the stale plan's retained catalog is merged into discovery
- **THEN** the model advertised only by the removed account leaves discovery
- **AND** the models still advertised by the remaining active account are retained

#### Scenario: Sole advertiser removed under a non-authoritative previous snapshot

- **GIVEN** a first refresh recorded a model advertised by one account of a plan
- **AND** a same-plan account had no catalog, so the snapshot is non-authoritative
- **WHEN** that sole advertiser is removed while another same-plan account stays active
- **AND** the plan's refresh does not complete in a later cycle
- **THEN** the model advertised only by the removed account still leaves discovery

#### Scenario: Removed catalog model stays suppressed across repeated partial refreshes

- **GIVEN** a snapshot suppressed a previously advertised catalog model because every last-known advertiser left the active account set
- **WHEN** later refresh cycles remain non-authoritative and still do not produce fresh active evidence for that model
- **THEN** the model stays absent from discovery and plan gating across those repeated partial refreshes

#### Scenario: Suppressed catalog model cannot select an account

- **GIVEN** a snapshot explicitly suppresses a previously advertised model because no active account advertises it
- **WHEN** account selection receives a request for that model
- **THEN** the selector rejects every account for that model
- **AND** it does not treat the known suppressed slug as an operator-mapped unknown

#### Scenario: First complete catalog suppresses omitted bootstrap model

- **GIVEN** there is no previous registry snapshot
- **AND** a bootstrap model slug is known to the proxy
- **WHEN** the first authoritative account-catalog refresh omits that model
- **THEN** the registry marks the omitted bootstrap slug as suppressed
- **AND** account selection does not treat that known slug as an operator-mapped unknown

#### Scenario: Fresh active evidence clears catalog suppression

- **GIVEN** a catalog model was previously suppressed after its last-known advertisers left the active account set
- **WHEN** a later refresh records that an active account advertises that model again
- **THEN** the suppression is cleared
- **AND** the model returns to discovery and plan gating from live registry data

#### Scenario: Never-known operator mapping remains distinct from suppression

- **GIVEN** an operator-mapped slug has never appeared in an account catalog
- **WHEN** an authoritative catalog snapshot does not contain that slug
- **THEN** the registry does not mark the slug as suppressed
- **AND** the existing operator-mapped unknown fallback remains available

#### Scenario: Carried-forward model has unknown per-account provenance

- **GIVEN** a plan-only snapshot carried a model with no per-account provenance
- **WHEN** the plan is stale in a later refresh that knows the active account set
- **THEN** the model is preserved in discovery rather than dropped

### Requirement: Speed and service tier metadata aggregates across accounts

When the model registry merges catalog entries for the same model slug fetched from multiple plans or accounts, the system MUST union the model's `service_tiers`, `additional_speed_tiers`, and `default_service_tier` metadata across all contributing entries rather than overwriting them with the last-fetched entry. A slug MUST expose a speed/service tier when at least one contributing account advertises it, so an account without Fast entitlement cannot remove Fast from the shared catalog served by `GET /v1/models` and `GET /backend-api/codex/models`. Union entries MUST be de-duplicated. All non-tier model fields MAY retain last-fetched values.

#### Scenario: An account without Fast does not hide Fast globally

- **GIVEN** one account/plan returns `gpt-5.5` with a `fast` service tier
- **AND** another account/plan returns `gpt-5.5` with no `fast` service tier
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the `gpt-5.5` entry includes the `fast` service tier
- **AND** the `fast` entry appears exactly once

#### Scenario: Shared tiers are not duplicated

- **GIVEN** two accounts both return `gpt-5.5` with the same `fast` service tier
- **WHEN** the registry merges the two catalog snapshots
- **THEN** the merged `gpt-5.5` service tiers contain a single `fast` entry

### Requirement: Codex clients receive the Codex catalog from /v1/models

When `GET /v1/models` is called with a non-empty `client_version` query parameter, the service MUST return the same Codex catalog payload as `GET /backend-api/codex/models`, including its `models` entries and the OpenAI-compatible `object`/`data` fields. When the parameter is absent or empty, the service MUST return the unchanged OpenAI-compatible list shape. API-key model filtering and visibility rules MUST apply in both cases.

#### Scenario: Codex client fetches its catalog through the /v1 base URL

- **GIVEN** a Codex client configured with `openai_base_url` pointing at this proxy
- **WHEN** it calls `GET /v1/models?client_version=0.144.1`
- **THEN** the response contains Codex catalog entries under `models`
- **AND** the payload equals the response of `GET /backend-api/codex/models`

#### Scenario: OpenAI-compatible clients are unaffected

- **GIVEN** an OpenAI-compatible client
- **WHEN** it calls `GET /v1/models` without a `client_version` parameter (or with an empty value)
- **THEN** the response keeps the `{"object": "list", "data": [...]}` shape

### Requirement: Model catalog entries preserve model-source identity

The model registry SHALL represent each catalog entry with explicit model-source
identity. Subscription-backed entries SHALL use a subscription source kind and
MUST continue to derive account/plan availability from the existing ChatGPT
account model registry refresh. OpenAI-compatible endpoint entries SHALL use an
OpenAI-compatible source kind and a stable source id. The model-source
abstraction MUST NOT require OpenAI-compatible sources to be represented as
`Account` rows.

#### Scenario: Subscription model keeps subscription source identity

- **WHEN** the existing model refresh loads `gpt-5.4` from ChatGPT/Codex account metadata
- **THEN** the registry entry has source kind `subscription`
- **AND** the entry remains eligible for existing account/plan routing

#### Scenario: OpenAI-compatible model keeps endpoint source identity

- **WHEN** an enabled OpenAI-compatible source defines model `local-coder`
- **THEN** the registry entry has source kind `openai_compatible`
- **AND** the entry references the source id for that endpoint
- **AND** no `Account` row is required for that source

### Requirement: /v1/models includes eligible OpenAI-compatible source models

`GET /v1/models` SHALL include enabled OpenAI-compatible source models alongside
subscription-backed public models when the authenticated API key is allowed to
see the model and source. Disabled sources and disabled source models MUST NOT be
listed. Source identity MAY be omitted from the public OpenAI-compatible model
payload, but internal filtering and routing MUST preserve it.

#### Scenario: API key sees assigned source model

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **AND** an API key is assigned to that source and allows `local-coder`
- **WHEN** the key calls `GET /v1/models`
- **THEN** the response includes `local-coder`

#### Scenario: API key cannot see unassigned source model

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **AND** an API key is scoped to a different source
- **WHEN** the key calls `GET /v1/models`
- **THEN** the response does not include `local-coder`

### Requirement: Codex-native catalog includes only Responses-capable source models

`GET /backend-api/codex/models` SHALL include OpenAI-compatible source models
only when the source explicitly declares Responses-compatible support. This
allows Codex model-picker entries for external providers without advertising
Chat Completions-only sources that cannot satisfy Codex-native Responses
requests. Disabled sources and disabled source models MUST NOT be listed.
Subscription-backed Codex catalog entries MUST continue to be listed through the
existing registry path. If a source model entry emits `model_provider`, it MUST
emit `codex-lb` and MUST NOT advertise the external upstream provider name.

#### Scenario: Responses-capable source is advertised to Codex-native clients

- **GIVEN** an enabled OpenAI-compatible source exposes model `deepseek-v4-flash`
- **AND** the source declares Responses-compatible support
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the response includes `deepseek-v4-flash`
- **AND** the model entry does not change the Codex provider away from `codex-lb`

#### Scenario: Chat-only source is not advertised to Codex-native clients

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **AND** the source declares Chat Completions support only
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the response does not include `local-coder`

