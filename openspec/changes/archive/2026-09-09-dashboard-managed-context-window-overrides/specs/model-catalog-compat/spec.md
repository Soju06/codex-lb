## ADDED Requirements

### Requirement: Per-model context window overrides are dashboard settings

The per-model reported context window override (`model_context_window_overrides`, `slug -> window`) MUST be managed from the dashboard as one row per slug in the `model_context_window_overrides` table (`slug`, `context_window`), resolved per slug as environment entry < dashboard row: a slug with a row reports the row's window, a slug without a row inherits the `CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES` entry for that slug, and a slug with neither has no override (the catalog reports the upstream window). The migration and the first boot MUST NOT copy the environment dict into rows. The settings API MUST expose the overrides under `GET /api/settings/model-context-window-overrides` as the merged per-slug list, each entry carrying `slug`, the effective `context_window`, a `source` of `"dashboard"` or `"env"`, and `env_value` (the environment entry for the slug, `null` when none); `PUT /api/settings/model-context-window-overrides/{slug}` with `{"context_window": n}` MUST create or replace the row for `slug` in a single upsert, so concurrent creates of the same absent slug both succeed, and `DELETE /api/settings/model-context-window-overrides/{slug}` MUST remove it (404 when no row exists, including for a slug that only the environment knows). A write MUST reject a `context_window` that is not an integer between 1 and 2147483647 (the width of the stored column) — a float, a numeric string and a boolean are rejected, not coerced — and a slug that is empty, longer than 256 characters, or contains whitespace or non-printable characters; a slug MUST NOT be trimmed into validity, and the slug segment MUST accept `/`. The catalog endpoints (`GET /v1/models`, `GET /backend-api/codex/models`) MUST resolve the merged overrides once per catalog build from a cached snapshot of the rows that the settings API invalidates durably through the cross-replica `settings` cache namespace on every write — an invalidation MUST NOT be overwritten by a load that was already in flight when it happened —, MUST NOT read the database per model entry, and MUST apply the existing `max_context_window` clamp to a dashboard value exactly as to an environment value. Each store and delete MUST be recorded as a `settings_changed` audit event naming `model_context_window_overrides` and the slug. A stored or deleted row takes effect on the next catalog request on every replica without a restart; when the database is unreachable past the snapshot's TTL the catalog MUST keep serving the last known rows rather than failing the request. The environment variable remains as a deprecated per-slug fallback for one release and is removed in the next minor.

#### Scenario: Dashboard row overrides the environment entry for its slug

- **GIVEN** the process environment sets `CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES={"gpt-5.4": 300000}` and the upstream catalog reports `gpt-5.4` with `context_window=272000`
- **WHEN** `PUT /api/settings/model-context-window-overrides/gpt-5.4` stores `{"context_window": 515000}`
- **THEN** the next `GET /backend-api/codex/models` reports `gpt-5.4` with `context_window=515000` without a restart
- **AND** `GET /api/settings/model-context-window-overrides` lists `gpt-5.4` with `context_window=515000`, `source="dashboard"` and `env_value=300000`

#### Scenario: Deleted row returns the slug to the environment entry

- **GIVEN** a dashboard row `gpt-5.4 -> 515000` while the environment entry for `gpt-5.4` is `300000`
- **WHEN** `DELETE /api/settings/model-context-window-overrides/gpt-5.4` succeeds
- **THEN** the next catalog request reports `gpt-5.4` with `context_window=300000`
- **AND** the settings API lists `gpt-5.4` with `source="env"` and `env_value=300000`

#### Scenario: Slug known only to the environment has no row to delete

- **GIVEN** the environment entry `gpt-5.4 -> 300000` and no dashboard row for `gpt-5.4`
- **WHEN** `DELETE /api/settings/model-context-window-overrides/gpt-5.4` is called
- **THEN** the response is 404 with code `model_context_window_override_not_found`
- **AND** the environment entry keeps applying

#### Scenario: Dashboard value is clamped like an environment value

- **GIVEN** the upstream catalog reports a model with `context_window=272000` and `max_context_window=872000`
- **WHEN** a dashboard row stores `1000000` for that slug
- **THEN** `GET /v1/models` and `GET /backend-api/codex/models` report `872000` for every context budget field of that model

#### Scenario: Invalid window or slug is rejected

- **WHEN** `PUT /api/settings/model-context-window-overrides/gpt-5.4` sends `{"context_window": 0}`, `-1`, `1.5`, `515000.0`, `"515000"`, `true` or `2147483648`
- **THEN** the response is 422 and no row is written
- **WHEN** the slug segment is empty, longer than 256 characters, or contains whitespace (including leading or trailing whitespace) or a control character
- **THEN** the response is 400 with code `invalid_model_slug` and no row is written

#### Scenario: Catalog build reads one cached snapshot

- **WHEN** `GET /v1/models` or `GET /backend-api/codex/models` builds its entries
- **THEN** the merged overrides are resolved once before the per-model loop from the cached row snapshot and the process settings
- **AND** no database read happens per model entry

## MODIFIED Requirements

### Requirement: OpenAI-compatible model metadata uses backend context windows

When serving `GET /v1/models`, the system SHALL expose `metadata.context_window` as the upstream backend `context_window` budget by default. The system MUST NOT promote raw `max_context_window` values or hard-coded full-context guesses into `metadata.context_window`. Explicit operator context-window overrides — a `model_context_window_overrides` dashboard row for the slug, else the `CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES` entry for the slug (see "Per-model context window overrides are dashboard settings") — remain the highest-priority reported-context value, clamped to the upstream-declared `max_context_window` when upstream declares one above the backend `context_window`.

#### Scenario: GPT-5 Codex models are reported with the backend context window on /v1/models

- **WHEN** the upstream model catalog contains `gpt-5.5`, `gpt-5.4-mini`, `gpt-5.3-codex`, or `gpt-5.4` with `context_window=272000`
- **THEN** `GET /v1/models` returns each entry with `metadata.context_window=272000`

#### Scenario: raw max_context_window does not inflate /v1/models context_window

- **WHEN** the upstream model catalog contains a model with `context_window=272000` and `max_context_window=900000`
- **THEN** `GET /v1/models` returns that entry with `metadata.context_window=272000`

### Requirement: OpenAI-compatible model metadata preserves the backend input budget explicitly

When serving `GET /v1/models`, the system SHALL expose the upstream backend input/context budget in `metadata.input_context_window`. When an explicit operator context-window override (dashboard row, else environment entry) applies to a model, that override SHALL be the reported input budget as well, clamped to the upstream-declared `max_context_window` when upstream declares one above the backend `context_window`, so `metadata.input_context_window` and the OpenAI-compatible `context_length`, `contextLength`, and `capabilities.context_length` fields never contradict `metadata.context_window` and never advertise more input than the backend sanctions. A `max_context_window` equal to the backend `context_window` — the parseability default synthesized for bootstrap and source-catalog models — MUST NOT clamp an override, so raise overrides for those models keep working. For models whose reported `metadata.context_window` is not operator-overridden, `metadata.context_window` and `metadata.input_context_window` SHOULD be equal. The system SHOULD expose `metadata.max_output_tokens` for known GPT-5 Codex models when that output-budget value is known; that value MUST NOT be used to inflate `metadata.context_window`.

#### Scenario: /v1/models exposes the 272k backend input budget explicitly

- **WHEN** the upstream model catalog contains a known GPT-5 Codex model with `context_window=272000`
- **THEN** `GET /v1/models` returns that model with `metadata.input_context_window=272000`
- **AND** `metadata.context_window=272000`

#### Scenario: Explicit reported-context overrides do not hide the backend input budget

- **WHEN** an operator override sets a model's reported `metadata.context_window` to `515000`
- **AND** the upstream model catalog contains that model with `context_window=272000` and no `max_context_window`
- **THEN** `GET /v1/models` returns that model with `metadata.context_window=515000`
- **AND** `metadata.input_context_window=515000`
- **AND** `context_length`, `contextLength`, and `capabilities.context_length` of `515000`

#### Scenario: An override never advertises more input than the backend ceiling

- **WHEN** an operator override sets a model's reported context window to `1000000`
- **AND** the upstream model catalog contains that model with `context_window=272000` and `max_context_window=872000`
- **THEN** `GET /v1/models` returns that model with `metadata.context_window=872000`
- **AND** `metadata.input_context_window=872000`
- **AND** `context_length`, `contextLength`, and `capabilities.context_length` of `872000`

#### Scenario: A synthesized ceiling equal to the backend budget does not clamp an override

- **WHEN** an operator override sets a source-catalog model's reported context window to `32768`
- **AND** that model declares `context_window=8192` and no explicit `max_context_window`, so the catalog synthesizes `max_context_window=8192`
- **THEN** `GET /v1/models` returns that model with `metadata.context_window=32768`
- **AND** `metadata.input_context_window=32768`
- **AND** `context_length`, `contextLength`, and `capabilities.context_length` of `32768`

#### Scenario: /v1/models exposes max output budget for known GPT-5 Codex models

- **WHEN** `GET /v1/models` returns `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, or `gpt-5.3-codex`
- **THEN** the entry's metadata includes `max_output_tokens=128000`

### Requirement: Codex-native model catalog keeps backend catalog fields

When serving `GET /backend-api/codex/models`, the system MUST keep Codex-native model catalog semantics unchanged: the top-level `context_window` field remains the backend compact/input budget unless an explicit operator override applies, and upstream raw fields such as `max_context_window` remain available when upstream provides them. The `/v1/models` compatibility metadata MUST NOT mutate the native Codex endpoint.

When an explicit operator context-window override (dashboard row, else environment entry) applies to a model, the native entry SHALL report the single resolved value — the override clamped to the upstream-declared `max_context_window` when upstream declares one above the backend `context_window`; a `max_context_window` equal to the backend `context_window` (the synthesized parseability default) MUST NOT clamp — on `context_window`, and SHALL rewrite `max_context_window` to that same resolved value when upstream provides the field. The endpoint's OpenAI-compatible `data` alias SHALL report the same resolved value on its `context_length`, `contextLength`, `capabilities.context_length`, `metadata.context_window`, and `metadata.input_context_window` fields, so the native and alias views of one model never advertise different budgets.

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

#### Scenario: Native Codex catalog reports one resolved budget for a clamped override

- **WHEN** an operator override sets a model's reported context window to `1000000`
- **AND** the upstream model catalog contains that model with `context_window=272000` and `max_context_window=872000`
- **THEN** `GET /backend-api/codex/models` returns that model with `context_window=872000`
- **AND** `max_context_window=872000`

#### Scenario: Codex data alias reports the resolved input budget for an override

- **WHEN** an operator override sets a model's reported context window to `515000`
- **AND** the upstream model catalog contains that model with `context_window=272000` and no explicit `max_context_window`
- **THEN** the `GET /backend-api/codex/models` `data` alias entry for that model reports `context_length`, `contextLength`, and `capabilities.context_length` of `515000`
- **AND** `metadata.context_window=515000` and `metadata.input_context_window=515000`
- **AND** the native `models` entry reports `context_window=515000`

### Requirement: GPT-5.6 bootstrap metadata matches the upstream bundled catalog

The GPT-5.6 bootstrap catalog entries (`gpt-5.6-sol`, `gpt-5.6-terra`,
`gpt-5.6-luna`) MUST mirror the upstream bundled catalog
(`codex-rs/models-manager/models.json` at Codex release `rust-v0.145.0`)
field-for-field for every metadata field codex-lb serves, with one tracked
exception: `max_context_window`, which upstream raised from `272000` to
`872000` in openai/codex commit
`2eee483e49f88b868f67364134a658b3298e6c14` (openai/codex#39102) and which no
`rust-v*` release tag carries as of `rust-v0.148.0-alpha.21`. In particular
each entry MUST carry: `context_window` of `272000` and `max_context_window`
of `872000`; `minimal_client_version` `"0.144.0"`; `tool_mode`
`"code_mode_only"`; `use_responses_lite` `true`; `apply_patch_tool_type`
`"freeform"`; `web_search_tool_type` `"text_and_image"`;
`supports_image_detail_original` `true`; `truncation_policy` `{ "mode":
"tokens", "limit": 10000 }`; `comp_hash` `"3000"`; `reasoning_summary_format`
`"experimental"`; `default_reasoning_summary` `"none"`;
`include_skills_usage_instructions` `false`; `experimental_supported_tools`
`[]` (a field the Codex client's deserializer requires); `supports_search_tool`
`true`; `additional_speed_tiers` `["fast"]`; the `priority`/`Fast` service tier
entry; `shell_type` `"shell_command"`; `prefer_websockets` `true`; and the
21-plan `available_in_plans` list upstream advertises (including `edu_plus`,
`edu_pro`, `enterprise_cbp_automation`, and `sci`). `multi_agent_version` MUST
be `"v2"` for Sol and Terra and `"v1"` for Luna. Sol MUST carry the upstream
`availability_nux` message while Terra and Luna carry `null`. Default reasoning
levels MUST be `low` for Sol and `medium` for Terra and Luna, and
reasoning-level descriptions MUST be the verbatim upstream strings.

`context_window` is the default input budget and `max_context_window` is the
ceiling a client may opt into; the two MUST NOT be collapsed into one value
for these entries.

The ~16.5 KB upstream `base_instructions` prompt and the personality-templated
`model_messages` object are deliberately NOT bundled in the bootstrap catalog;
the first successful live registry refresh supplies them. This is the only
sanctioned divergence from the upstream GPT-5.6 entries beyond the
`max_context_window` exception above.

#### Scenario: GPT-5.6 bootstrap entries retain the corrected upstream context budget

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** no persisted snapshot is loaded
- **AND** no context-window override (dashboard row or `CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES` entry) applies to these slugs
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` report
  `context_window=272000`

#### Scenario: GPT-5.6 bootstrap entries advertise the raised upstream ceiling

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** no persisted snapshot is loaded
- **AND** no context-window override (dashboard row or `CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES` entry) applies to these slugs
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` report
  `context_window=272000`
- **AND** each reports `max_context_window=872000`

#### Scenario: OpenAI-compatible metadata keeps the default input budget

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** no persisted snapshot is loaded
- **AND** no context-window override (dashboard row or `CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES` entry) applies to these slugs
- **WHEN** a client calls `GET /v1/models`
- **THEN** each GPT-5.6 entry reports `context_window=272000` and
  `input_context_window=272000`
- **AND** the raised Codex-native ceiling is not promoted into the
  OpenAI-compatible input budget fields

#### Scenario: GPT-5.6 entries expose upstream tool and multi-agent metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** no persisted snapshot is loaded
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` carry `tool_mode: "code_mode_only"`, `use_responses_lite: true`, `experimental_supported_tools: []`, and `minimal_client_version: "0.144.0"`
- **AND** `multi_agent_version` is `"v2"` for Sol and Terra and `"v1"` for Luna

#### Scenario: GPT-5.6 entries expose upstream reasoning-summary and plan metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** no persisted snapshot is loaded
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** each GPT-5.6 entry carries `default_reasoning_summary: "none"`, `reasoning_summary_format: "experimental"`, and `comp_hash: "3000"`
- **AND** each GPT-5.6 entry's `available_in_plans` includes `edu_plus`, `edu_pro`, `enterprise_cbp_automation`, and `sci`
- **AND** only `gpt-5.6-sol` carries a non-null `availability_nux` message
