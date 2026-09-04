## MODIFIED Requirements

### Requirement: Bootstrap model catalog is available before refresh

Before the first successful upstream model-registry refresh, the system MUST
serve a conservative static catalog of known Codex model slugs from both
`GET /v1/models` and `GET /backend-api/codex/models`. This static catalog is a
bundled fallback for startup/offline paths; refreshed upstream model-registry
data remains the authoritative source once available. A replica that starts
while a fresh persisted registry snapshot exists SHALL serve the persisted
catalog (not the bootstrap catalog) before its first scheduler tick; the
bootstrap catalog remains the floor only when no persisted or refreshed
snapshot is available. The bootstrap catalog MUST include `gpt-6-astra`,
`gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.4`,
`gpt-5.4-mini`, `gpt-5.3-codex`, `gpt-5.3-codex-spark`, `gpt-5.2`, and
`codex-auto-review`, and MUST NOT invent unverified variant slugs such as
`gpt-5.5-pro`, a bare `gpt-5.6`, or a bare `gpt-6`. `gpt-5.3-codex` and
`gpt-5.3-codex-spark` were dropped from upstream's bundled catalog at codex
rust-v0.144.x but remain retained for older pinned clients because the upstream
backend still serves them.

#### Scenario: OpenAI-compatible models endpoint serves bootstrap slugs

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** no persisted registry snapshot is available
- **WHEN** a client calls `GET /v1/models`
- **THEN** the response contains exactly the bootstrap model slugs
- **AND** the response includes `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`
- **AND** the response does not include `gpt-5.5-pro`, bare `gpt-5.6`, or bare `gpt-6`

#### Scenario: Codex-native models endpoint serves GPT-5.6 bootstrap metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` entries include representative upstream metadata including context-window, visibility, speed-tier, and reasoning fields
- **AND** Sol and Terra advertise `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`
- **AND** Luna advertises `low`, `medium`, `high`, `xhigh`, and `max`

#### Scenario: Replica startup with a fresh persisted snapshot serves the persisted catalog

- **GIVEN** a fresh persisted registry snapshot exists whose catalog differs from the bootstrap catalog
- **WHEN** a replica starts and a client calls `GET /v1/models` before the first refresh tick
- **THEN** the response reflects the persisted catalog, not the bootstrap catalog

### Requirement: Fallback client version covers the bootstrap catalog

The configured fallback Codex client version MUST be greater than or equal to
`0.153.0`, the highest `minimal_client_version` in the bootstrap catalog, so a
degraded-startup registry refresh still receives `gpt-6-astra` from upstream.

#### Scenario: Degraded-startup refresh still requests GPT-6 Astra

- **GIVEN** the live Codex release lookup fails and no version is cached
- **WHEN** the model registry refresh fetches `<base>/codex/models?client_version=<fallback>`
- **THEN** the fallback version is at least `0.153.0` (`gpt-6-astra`'s `minimal_client_version`)

## ADDED Requirements

### Requirement: GPT-6 Astra bootstrap metadata matches the captured upstream catalog

The `gpt-6-astra` bootstrap catalog entry MUST mirror the upstream catalog
entry captured from the live proxy on 2026-09-05 Asia/Tbilisi time
(2026-09-04 UTC) for metadata fields codex-lb serves before account catalogs
are authoritative. It MUST carry: `priority` `1`; `visibility` `"list"`;
`context_window` `272000`;
`max_context_window` `872000`; `minimal_client_version` `"0.153.0"`;
`prefer_websockets` `true`; `tool_mode` `"code_mode_only"`;
`multi_agent_version` `"v2"`; `use_responses_lite` `true`;
`apply_patch_tool_type` `"freeform"`; `web_search_tool_type`
`"text_and_image"`; `supports_image_detail_original` `true`;
`default_service_tier` `"priority"`; the `priority` service tier named
`"Fast"` with description `"2x speed, increased usage"`; and
`additional_speed_tiers` containing `"fast"`.

The `gpt-6-astra` entry MUST advertise reasoning levels `low`, `medium`,
`high`, `xhigh`, `max`, and `ultra`, with default reasoning level `medium`.
It MUST advertise every plan from the captured upstream entry, including
`free`, `free_workspace`, `plus`, `pro`, `team`, `business`, and `enterprise`.

#### Scenario: GPT-6 Astra appears in bootstrap catalog before refresh

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** no persisted snapshot is loaded
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the response contains `gpt-6-astra`
- **AND** that entry reports `context_window=272000` and `max_context_window=872000`
- **AND** that entry reports `minimal_client_version="0.153.0"`

#### Scenario: GPT-6 Astra exposes upstream reasoning and service-tier metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** no persisted snapshot is loaded
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** `gpt-6-astra` advertises `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`
- **AND** its default reasoning level is `medium`
- **AND** its default service tier is `priority`
- **AND** its `priority` service tier is named `Fast` with description `2x speed, increased usage`

#### Scenario: GPT-6 Astra bootstrap websocket preference is honored

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** websocket preference is checked for `gpt-6-astra` or a `gpt-6-*` model slug
- **THEN** the lookup returns true
